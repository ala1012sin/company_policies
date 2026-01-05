from fastapi import FastAPI, HTTPException
from fastmcp import FastMCP
import json
import os
from pdf_chunking import collection
from typing import List, Dict, Any, Optional
import re
from Ocr_Recorder import _decode_image_b64, _ocr_lines, _extract_fields
from PIL import Image
from jinja2 import Template

mcp = FastMCP("MES-MCP")
mcp_app = mcp.http_app()

app = FastAPI(
    title="MES API + MCP",
    description="REST API와 MCP를 동시에 제공하는 통합 서버",
    version="1.0.0",
    lifespan=mcp_app.lifespan
)



@mcp.tool()
def searcing_chromadb(query: str, top_k: int = 5):
    """ChromaDB에서 회사 내규 문서를 검색합니다."""
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas"]
    )
    response = []
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        response.append({
            "document": doc,
            "metadata": meta
        })
    return json.dumps(response, ensure_ascii=False, indent=2)

@mcp.tool()
def extract_receipt_core_fields(image_b64: str, mime_type: str = "image/jpeg", language: List[str] = ["ko", "en"]) -> Dict[str, Any]:
    """
    영수증 이미지(base64)를 입력받아 필수 필드를 추출합니다.
    - 거래일자, 결제금액, 가맹점 정보(이름/전화), 결제수단, 사업자번호
    
    OCR 실패 시 ask_for_missing_field 도구로 사용자에게 정보를 요청합니다.
    """
    img_np = _decode_image_b64(image_b64)
    lines = _ocr_lines(img_np)
    parsed = _extract_fields(lines)
    parsed["raw_lines"] = [{"text": l["text"], "conf": l["conf"]} for l in lines]
    
    # 부족한 필드 확인
    missing_fields = []
    if not parsed.get("business_reg_no"):
        missing_fields.append("사업자번호")
    if not parsed.get("trade_date"):
        missing_fields.append("거래일자")
    if not parsed.get("amount"):
        missing_fields.append("결제금액")
    if not parsed.get("merchant", {}).get("name"):
        missing_fields.append("가맹점명")
    
    if missing_fields:
        parsed["missing_fields"] = missing_fields
        parsed["user_input_required"] = True
    else:
        parsed["user_input_required"] = False
    
    return parsed

@mcp.tool()
def ask_for_missing_field(field_name: str, instruction: str = "") -> Dict[str, str]:
    """
    OCR 추출 실패 시 사용자에게 정보를 요청합니다.
    
    Args:
        field_name: 요청할 필드명 (예: "사업자번호", "거래일자")
        instruction: 추가 안내사항
    
    Returns:
        사용자가 입력한 값
    """
    field_prompts = {
        "사업자번호": "사업자번호를 입력해주세요 (예: 123-45-67890 또는 1234567890)",
        "거래일자": "거래일자를 입력해주세요 (예: 2025-01-05 또는 2025/01/05)",
        "결제금액": "결제금액을 입력해주세요 (예: 32000 또는 32,000)",
        "가맹점명": "가맹점명(상호)을 입력해주세요",
        "가맹점전화": "가맹점 전화번호를 입력해주세요 (예: 02-1234-5678)",
    }
    
    prompt = field_prompts.get(field_name, instruction or field_name)
    
    return {
        "field": field_name,
        "prompt": prompt,
        "status": "awaiting_user_input"
    }

@mcp.tool()
def update_receipt_fields(parsed_data: Dict[str, Any], field_updates: Dict[str, str]) -> Dict[str, Any]:
    """
    OCR 추출 결과에 사용자 입력 정보를 병합합니다.
    
    Args:
        parsed_data: OCR 추출 결과
        field_updates: 사용자가 입력한 필드 업데이트 (예: {"사업자번호": "123-45-67890"})
    
    Returns:
        업데이트된 영수증 데이터
    """
    result = parsed_data.copy()
    
    for field, value in field_updates.items():
        if field == "사업자번호":
            # 형식 정규화
            value = re.sub(r"[^0-9-]", "", value)
            if len(re.sub(r"-", "", value)) == 10:
                # XXX-XX-XXXXX 형식으로 정규화
                nums = re.sub(r"-", "", value)
                result["business_reg_no"] = f"{nums[:3]}-{nums[3:5]}-{nums[5:10]}"
                result["confidence"]["business_reg_no"] = 1.0  # 사용자 입력이므로 신뢰도 최대
            else:
                result["business_reg_no"] = value
                result["confidence"]["business_reg_no"] = 0.95
                
        elif field == "거래일자":
            # ISO 형식으로 정규화
            if "/" in value:
                value = value.replace("/", "-")
            result["trade_date"] = value
            result["confidence"]["trade_date"] = 1.0
            
        elif field == "결제금액":
            # 숫자만 추출
            num_str = re.sub(r"[^0-9]", "", value)
            if num_str:
                result["amount"] = int(num_str)
                result["confidence"]["amount"] = 1.0
                
        elif field == "가맹점명":
            result["merchant"]["name"] = value
            result["confidence"]["merchant_name"] = 1.0
            
        elif field == "가맹점전화":
            result["merchant"]["tel"] = value
            result["confidence"]["merchant_tel"] = 1.0
    
    # 부족한 필드 제거
    result.pop("missing_fields", None)
    result.pop("user_input_required", None)
    
    return result

@mcp.tool()
def generate_cost_html(receipt_data: Dict[str, Any], user_info: Dict[str, str] = None) -> str:
    """
    OCR 추출 영수증 데이터를 지출결의서(cost.html) 템플릿에 자동으로 채웁니다.
    
    Args:
        receipt_data: extract_receipt_core_fields() 결과
        user_info: 추가 사용자 정보 
                  (문서번호, 결재자, 작성자, 신청부서, 신청일자 등)
    
    Returns:
        채워진 HTML 문자열
    """
    if user_info is None:
        user_info = {}
    
    # 템플릿 파일 읽기
    template_path = os.path.join(os.path.dirname(__file__), "template", "cost.html")
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()
    
    # 치환 데이터 준비
    receipt_date = receipt_data.get("trade_date", "")
    receipt_amount = receipt_data.get("amount", 0)
    merchant_name = receipt_data.get("merchant", {}).get("name", "")
    merchant_tel = receipt_data.get("merchant", {}).get("tel", "")
    payment_method = receipt_data.get("payment_method", "")
    business_reg_no = receipt_data.get("business_reg_no", "")
    
    # 결제수단 매핑
    payment_checkbox = ""
    if payment_method == "card":
        payment_checkbox = '<label class="check-item"><input type="checkbox" checked> 법인카드</label>'
    elif payment_method == "cash":
        payment_checkbox = '<label class="check-item"><input type="checkbox" checked> 현금</label>'
    elif payment_method == "app_pay":
        payment_checkbox = '<label class="check-item"><input type="checkbox" checked> 개인카드</label>'
    
    # 치환 맵
    replacements = {
        # 상단 정보
        'placeholder="예: FIN-EXP-2025-001"': f'value="{user_info.get("문서번호", "")}"',
        'placeholder="예: 홍길동"': f'value="{user_info.get("결재자", "")}"',
        'placeholder="예: 2025-12-30"': f'value="{user_info.get("결재일자", "")}"',
        'placeholder="예: 김OO"': f'value="{user_info.get("작성자", "")}"',
        
        # 기본정보
        'placeholder="예: 재무회계팀"': f'value="{user_info.get("신청부서", "")}"',
        'placeholder="예: 생산팀 / 대리"': f'value="{user_info.get("소속_직급", "")}"',
        
        # 지출 내역 - 첫 번째 행 (영수증 데이터 채우기)
    }
    
    result = template_content
    for old, new in replacements.items():
        result = result.replace(old, new)
    
    # 지출 내역 첫 번째 행 채우기 (식대로 분류)
    expense_row = f'''      <tr>
        <td><label class="check-item"><input type="checkbox" checked> 식대</label></td>
        <td><input class="field" value="{receipt_date}" placeholder="YYYY-MM-DD"></td>
        <td><input class="field" value="{merchant_name}" placeholder="예: 회의 식비"></td>
        <td><input class="field right" value="{receipt_amount:,}" placeholder="0"></td>
        <td><input class="field" value="사업자: {business_reg_no}, 전화: {merchant_tel}" placeholder=""></td>
      </tr>'''
    
    # 원래의 식대 행을 교체
    old_exp_row = '''      <tr>
        <td><label class="check-item"><input type="checkbox"> 식대</label></td>
        <td><input class="field" placeholder="YYYY-MM-DD"></td>
        <td><input class="field" placeholder="예: 회의 식비"></td>
        <td><input class="field right" placeholder="0"></td>
        <td><input class="field" placeholder=""></td>
      </tr>'''
    result = result.replace(old_exp_row, expense_row, 1)
    
    # 합계 금액 채우기
    result = result.replace(
        '<input class="field right" style="width:70%;" placeholder="0 원" />',
        f'<input class="field right" style="width:70%;" value="{receipt_amount:,} 원" />'
    )
    
    # 결제정보 섹션
    result = result.replace(
        '<input class="field right" placeholder="0"></td>\n      </tr>\n\n      <tr>\n        <td class="label">결제수단</td>',
        f'<input class="field right" value="{receipt_amount}"></td>\n      </tr>\n\n      <tr>\n        <td class="label">결제수단</td>'
    )
    
    # 결제수단 체크박스 교체
    old_payment = '''        <td class="value" colspan="3">
          <div class="checks">
            <label class="check-item"><input type="checkbox"> 법인카드</label>
            <label class="check-item"><input type="checkbox"> 개인카드</label>
            <label class="check-item"><input type="checkbox"> 현금</label>
          </div>
        </td>'''
    
    new_payment = f'''        <td class="value" colspan="3">
          <div class="checks">
            {payment_checkbox}
          </div>
        </td>'''
    
    result = result.replace(old_payment, new_payment)
    
    return result




app.mount("/", mcp_app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
