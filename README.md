## 가상환경(venv) 설정

### 1) 가상환경 생성
```powershell
cd C:\Rag_Project
python -m venv .venv
```
### 2) 가상환경 활성화
```powershell
.\.venv\Scripts\Activate.ps1
```
### 3) 패키지 설치
```powershell
python -m pip install -U pip
pip install -r .\requirements.txt
```
### 4) 가상환경 비활성화(나가기)
```powershell
deactivate
```
## Git 사용법 (브랜치 생성 + 코드 가져오기)
1) 저장소 처음 받기(클론)
```powershell
git clone https://github.com/ala1012sin/company_policies.git
cd company_policies
```
2) 새 브랜치 만들고 이동
```powershell
git checkout -b feature/my-work
```
3) GitHub 최신 코드 가져오기(pull)
```powershell
git pull origin main
```
4) (선택) 내가 만든 브랜치 GitHub에 올리기(push)
```powershell
git push -u origin feature/my-work
```
