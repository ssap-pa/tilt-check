# tilt-check 설치·사용 안내 (한국어)

매매 기록을 읽고 "네 기록은 이렇게 말한다"를 보여 주는 도구입니다. 주문은 절대 내지 않습니다.

## 1. 준비물

- Windows PC (NinjaTrader 8이 깔린 그 PC가 가장 편합니다)
- Python 3.11 이상: https://www.python.org/downloads/ 에서 받아 설치할 때 **"Add python.exe to PATH"** 체크
- Git: https://git-scm.com/download/win (기본값으로 설치)
- (선택) Ollama + Gemma: `check`·`report` 명령의 한국어 설명문에만 필요합니다. 수치 분석(`audit`, `audit-report`, `exits`, `watch`)은 없어도 됩니다.

## 2. 설치 (한 번만)

PowerShell을 열고:

```powershell
cd $HOME
git clone https://github.com/ssap-pa/tilt-check.git
cd tilt-check
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

설치 확인(가짜 샘플 데이터로):

```powershell
.venv\Scripts\python -m tiltcheck report --csv sample\trades_sample.csv --no-llm
```

표가 찍히면 됩니다.

## 3. NinjaTrader에서 내보내기

**체결 내역(필수)**: Control Center → New → Trade Performance → 기간 선택 → Trades 탭 → 표에서 오른쪽 클릭 → Export → CSV로 저장. 한글 열 이름 그대로 됩니다.

**1분봉(권장)**: Control Center → Tools → Historical Data → Export → 종목(MNQ 12-26 등), Minute, 같은 기간 → 저장. 종목마다 `MNQ 12-26.Last.txt` 같은 파일이 생깁니다. 한 폴더에 모아 두세요. (10/4에 보내 주신 `instrument,bar_end_utc,...` 형식의 CSV 한 장도 그대로 읽습니다.)

## 4. 돌려 보기

```powershell
cd $HOME\tilt-check
# 전체 리포트(마크다운 한 장). --bars 는 1분봉 파일이나 폴더
.venv\Scripts\python -m tiltcheck audit-report --csv "C:\경로\trades.csv" --account 0014 --bars "C:\경로\bars" --out report.md

# 원칙 대비 실제(15분 200선, 5분 100EMA, VWAP 밴드 등)
.venv\Scripts\python -m tiltcheck audit --csv "C:\경로\trades.csv" --account 0014 --bars "C:\경로\bars"

# 청산 재생(고정 익절/손절 브래킷 vs 본인 청산, 70/30 검증)
.venv\Scripts\python -m tiltcheck exits --csv "C:\경로\trades.csv" --account 0014 --bars "C:\경로\bars"
```

`--account 0014`는 계좌번호 끝자리로 그 계좌만 고릅니다. `report.md`는 메모장이나 VS Code로 열면 됩니다.

## 5. 감시 모드 (거래 중 알림)

아직 실제 NinjaTrader 8에서 돌려 보지 못했습니다(코드는 스텁으로 컴파일만 확인). 처음 설치할 때 문제가 나면 그 메시지를 그대로 보내 주세요.

1. `ninjatrader\TiltCheckFeed.cs` 파일을 `문서\NinjaTrader 8\bin\Custom\AddOns\` 폴더에 복사
2. NinjaTrader → New → NinjaScript Editor → F5(컴파일). 오류가 없으면 NinjaTrader를 껐다 켭니다
3. 거래가 체결되면 `문서\tilt-check\fills.csv`(체결), `orders.csv`(주문), `snapshots.csv`(미실현 손익)가 자동으로 쌓입니다
4. 규칙 파일을 만듭니다: `%USERPROFILE%\.tilt-check\rules.json` (예시는 README의 Watch mode 절. 마이크로 20·미니 2계약, 손실 뒤 15분 멈춤, 드로다운 기준 1%·15% 등)
5. 감시 시작:

```powershell
.venv\Scripts\python -m tiltcheck watch --csv "C:\경로\trades.csv" --account 0014 --bars "C:\경로\bars"
```

`--bars`를 주면 진입마다 `CHART:` 줄이 하나 더 붙습니다: 15분 200EMA 기준 추세 순/역, 세션 VWAP에서 몇 sd 떨어졌는지, 추격인지 페이드인지(닫힌 봉만 사용). 규칙 파일에 `"with_15m_200": true`(추세 역행 경고), `"vwap_sd_max": 2`(그 이상 추격 경고), `"fade_inside_1sd": true`(첫 밴드 안 페이드 경고)를 넣으면 경고도 뜹니다. 지금은 내보낸 봉 파일을 읽으므로 거래 전에 최신 봉을 한 번 내보내 두면 됩니다(실시간 봉은 다음 단계).

규칙을 어기는 체결이 들어오면 Windows 알림이 뜹니다(긴급이면 소리). 진입마다 "평소 익절/손절 중간값" 한 줄도 조용히 보여 줍니다. 주문을 내거나 바꾸거나 취소하는 기능은 없습니다.

지난 날짜로 미리 보기: `--replay-day 2026-10-01` 을 붙이면 그날 체결을 순서대로 흘려서 어떤 알림이 떴을지 보여 줍니다.

## 6. 새 내보내기가 생기면

```powershell
.venv\Scripts\python -m tiltcheck learn --csv "C:\경로\새_trades.csv"
```

기록이 합쳐지고, 그 뒤 `check`·`watch`가 새 기록까지 읽습니다.

## 7. 데이터는 어디에 남나

전부 이 PC 안에만 있습니다. `~/.tilt-check/`(기록·결정 로그), `문서\tilt-check\`(애드온 파일). 서버로 보내는 것은 없습니다. 선택한 Ollama/Gemma도 로컬에서 돕니다.
