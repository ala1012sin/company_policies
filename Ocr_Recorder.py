import easyocr
import base64
import io
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from PIL import Image


reader = easyocr.Reader(["ko", "en"], gpu=False)

# 영수증 검증 정규식

DATE_PAT = re.compile(r"(20\d{2})\s*[.\-/년 ]\s*(\d{1,2})\s*[.\-/월 ]\s*(\d{1,2})\s*(?:일)?")
BIZNO_PAT = re.compile(r"\b(\d{3})[- ]?(\d{2})[- ]?(\d{5})\b")
AMOUNT_PAT = re.compile(r"(합계|총액|결제금액|결제요금|승인금액|거래금액|미터요금|미터\s*요금|층\s*운임|총\s*운임)\s*[:\-]?\s*([0-9,]+)\s*[원온]?")
CARD_HINT = re.compile(r"(카드|신용|체크|승인|VISA|MASTER|AMEX)", re.I)
CASH_HINT = re.compile(r"(현금|현금영수증)", re.I)
APP_HINT = re.compile(r"(페이|PAY|간편결제|삼성페이|카카오페이|네이버페이|토스페이)", re.I)
TEL_PAT = re.compile(r"\b0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}\b")

def _decode_image_b64(image_b64: str) -> np.ndarray:
    img_bytes = base64.b64decode(image_b64)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    return np.array(img)


def _ocr_lines(img_np: np.ndarray) -> List[Dict[str, Any]]:
    """
    Returns: [{"text": str, "conf": float, "bbox": [[x,y]...]}]
    """
    results = reader.readtext(img_np, detail=1)
    lines = []
    for bbox, text, conf in results:
        t = (text or "").strip()
        if not t:
            continue
        lines.append({"text": t, "conf": float(conf), "bbox": bbox})
    return lines


def _pick_best_by_keyword(lines: List[Dict[str, Any]], keyword_regex: re.Pattern, value_regex: re.Pattern) -> Tuple[Optional[str], float]:
    """
    Find lines containing keyword and extract value using value_regex; return best by conf.
    """
    best_val, best_conf = None, 0.0
    for ln in lines:
        if not keyword_regex.search(ln["text"]):
            continue
        m = value_regex.search(ln["text"])
        if not m:
            continue
        val = m.group(1) if m.lastindex else m.group(0)
        if ln["conf"] > best_conf:
            best_val, best_conf = val, ln["conf"]
    return best_val, best_conf


def _extract_fields(lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    text_all = "\n".join([l["text"] for l in lines])

    warnings: List[str] = []
    confidence: Dict[str, float] = {}

    # 사업자번호
    biz = None
    biz_conf = 0.0
    
    # 1단계: 정규식으로 정확한 XXX-XX-XXXXX 찾기
    m = BIZNO_PAT.search(text_all)
    if m:
        biz = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        for ln in lines:
            if m.group(0).replace(" ", "") in ln["text"].replace(" ", ""):
                biz_conf = max(biz_conf, ln["conf"])
    
    # 2단계: "사업자번호" 키워드 다음 라인 확인 (OCR 오류 수정 포함)
    if not biz:
        for i, ln in enumerate(lines[:-1]):
            if "사업자" in ln["text"] and "번호" in ln["text"]:
                next_ln = lines[i + 1]
                next_text = next_ln["text"]
                
                # 숫자 추출
                numbers = re.findall(r"\d+", next_text)
                
                # 10자리 이상의 연속 숫자가 있으면 우선 사용
                if numbers and len(numbers[0]) >= 10:
                    num_str = numbers[0]
                    biz = f"{num_str[:3]}-{num_str[3:5]}-{num_str[5:10]}"
                    biz_conf = next_ln["conf"]
                else:
                    # OCR 오류: 숫자처럼 보이지만 문자인 경우 변환
                    # D→3, b→6, A→4, I→1, E→3, O→0, l→1, S→5, Z→2 등
                    ocr_to_num = {
                        'O': '0', 'o': '0', 'D': '3', 'd': '3',
                        'l': '1', 'I': '1', 'i': '1', 'L': '1',
                        'S': '5', 's': '5', 'Z': '2', 'z': '2',
                        'B': '8', 'b': '8', 'A': '4', 'a': '4',
                        'E': '3', 'e': '3', 'G': '6', 'g': '6',
                        'T': '7', 't': '7',
                    }
                    
                    # 문자를 숫자로 변환 시도
                    converted = next_text
                    for char, digit in ocr_to_num.items():
                        converted = converted.replace(char, digit)
                    
                    # 숫자만 추출
                    num_only = re.sub(r"\D", "", converted)
                    
                    if len(num_only) >= 10:
                        biz = f"{num_only[:3]}-{num_only[3:5]}-{num_only[5:10]}"
                        biz_conf = next_ln["conf"]
                    elif len(num_only) >= 8:
                        biz = num_only
                        biz_conf = next_ln["conf"]
                break
    
    # 3단계: 영수증 전체에서 정확한 사업자번호 형식 찾기
    if not biz:
        loose_pattern = re.compile(r"(\d{3})[- ](\d{2})[- ](\d{5})")
        for ln in lines:
            mm = loose_pattern.search(ln["text"])
            if mm:
                biz = f"{mm.group(1)}-{mm.group(2)}-{mm.group(3)}"
                biz_conf = ln["conf"]
                break
    
    # 4단계: 숫자만 연속된 10자리 이상 찾기 (마지막 수단)
    if not biz:
        for ln in lines:
            num_only = re.sub(r"\D", "", ln["text"])
            if len(num_only) >= 10:
                biz = f"{num_only[:3]}-{num_only[3:5]}-{num_only[5:10]}"
                biz_conf = ln["conf"]
                break
    
    if not biz:
        warnings.append("사업자번호를 찾지 못했습니다.")
    confidence["business_reg_no"] = round(biz_conf, 3)

    # 거래일자
    trade_date = None
    date_conf = 0.0
    # 여러 줄에서 가장 conf 높은 날짜 선택
    for ln in lines:
        mm = DATE_PAT.search(ln["text"])
        if not mm:
            continue
        y, mo, d = mm.group(1), int(mm.group(2)), int(mm.group(3))
        cand = f"{y}-{mo:02d}-{d:02d}"
        if ln["conf"] > date_conf:
            trade_date, date_conf = cand, ln["conf"]
    if not trade_date:
        warnings.append("거래일자를 찾지 못했습니다.")
    confidence["trade_date"] = round(date_conf, 3)

    # 결제금액(합계/총액 우선) - 여러 줄 처리
    amount = None
    amt_conf = 0.0
    
    # 먼저 전체 텍스트에서 매칭 시도
    mm = AMOUNT_PAT.search(text_all)
    if mm:
        val = mm.group(2).replace(",", "")
        try:
            amount = int(val)
            amt_conf = 0.8  # 전체 텍스트 매칭 신뢰도
        except ValueError:
            pass
    
    # 실패하면 각 줄별로 시도
    if amount is None:
        for ln in lines:
            mm = AMOUNT_PAT.search(ln["text"].replace(" ", ""))
            if not mm:
                continue
            val = mm.group(2).replace(",", "")
            try:
                cand = int(val)
            except ValueError:
                continue
            if ln["conf"] > amt_conf:
                amount, amt_conf = cand, ln["conf"]
    
    # 여전히 못 찾으면 "원" 또는 "온" 또는 "O" 앞의 큰 숫자 찾기 (택시 등)
    # O(영문)와 0(숫자)을 모두 처리, 더 큰 금액 우선
    if amount is None:
        amount_pattern = re.compile(r"([0-9,]+)\s*[원온O]")  # O도 추가 (OCR 오류)
        candidates = []
        for ln in lines:
            for match in amount_pattern.finditer(ln["text"]):
                val = match.group(1).replace(",", "")
                try:
                    num = int(val)
                    if num > 1000:  # 1000원 이상만
                        candidates.append((num, ln["conf"]))
                except ValueError:
                    pass
        
        if candidates:
            # 가장 큰 금액 우선 (신뢰도 동일시 가장 큰 금액)
            amount, amt_conf = max(candidates, key=lambda x: (x[1], x[0]))
    else:
        # 이미 찾았어도, 더 큰 금액이 있는지 확인 (정확도 개선)
        amount_pattern = re.compile(r"([0-9,]+)\s*[원온O]")
        for ln in lines:
            for match in amount_pattern.finditer(ln["text"]):
                val = match.group(1).replace(",", "")
                try:
                    num = int(val)
                    # 현재값의 2배 이상 큰 금액 발견시 교체
                    if num > amount * 1.5 and num > 1000:
                        amount, amt_conf = num, ln["conf"]
                except ValueError:
                    pass
    
    if amount is None:
        warnings.append("결제금액(합계/총액)을 찾지 못했습니다.")
    confidence["amount"] = round(amt_conf, 3)

    # 결제수단(룰 기반)
    payment_method = "unknown"
    pm_conf = 0.0
    if APP_HINT.search(text_all):
        payment_method = "app_pay"
        pm_conf = 0.7
    if CARD_HINT.search(text_all):
        payment_method = "card"
        pm_conf = max(pm_conf, 0.7)
    if CASH_HINT.search(text_all):
        payment_method = "cash"
        pm_conf = max(pm_conf, 0.7)
    confidence["payment_method"] = round(pm_conf, 3)
    if payment_method == "unknown":
        warnings.append("결제수단을 확정하지 못했습니다(카드/현금/앱결제).")

    # 가맹점 정보(매우 러프한 MVP: 상단 10줄 중 '영수증/합계/승인' 같은 키워드 제외)
    merchant_name = None
    m_conf = 0.0
    exclude = re.compile(r"(영수증|매출|합계|총액|승인|결제|금액|VAT|사업자|대표|부가세|카드|현금)", re.I)
    for ln in lines[:12]:
        t = ln["text"]
        if exclude.search(t):
            continue
        if len(t) < 2:
            continue
        merchant_name = t
        m_conf = ln["conf"]
        break
    if not merchant_name:
        warnings.append("가맹점명을 확정하지 못했습니다.")
    confidence["merchant_name"] = round(m_conf, 3)

    # 전화번호(있으면)
    tel = None
    tel_conf = 0.0
    for ln in lines:
        mm = TEL_PAT.search(ln["text"])
        if not mm:
            continue
        tel = mm.group(0).replace(" ", "-")
        tel_conf = max(tel_conf, ln["conf"])
    confidence["merchant_tel"] = round(tel_conf, 3)

    return {
        "trade_date": trade_date,
        "amount": amount,
        "merchant": {
            "name": merchant_name,
            "address": None,   # MVP: 다음 단계에서 bbox/키워드로 개선
            "tel": tel
        },
        "payment_method": payment_method,
        "business_reg_no": biz,
        "confidence": confidence,
        "warnings": warnings,
    }