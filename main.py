import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import httpx
import json
import urllib.request
import ssl
from datetime import datetime
from io import BytesIO
from PIL import Image

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ==========================================
# 強震モニタ用の事前準備[cite: 3]
# ==========================================
STATIONS_FILE = "stations.json"
try:
    with open(STATIONS_FILE, "r", encoding="utf-8") as f:
        OBSERVATION_POINTS = json.load(f)
except Exception as e:
    print(f"エラー: {STATIONS_FILE} が見つからないか、読み込めません。({e})")
    OBSERVATION_POINTS = []

def latlng_to_pixel(lat, lng):
    x = int((lng - 128.0) / (146.0 - 128.0) * 352)
    y = int((45.5 - lat) / (45.5 - 30.0) * 400)
    return max(0, min(x, 351)), max(0, min(y, 399))

def color_to_shindo(r, g, b):
    if (r, g, b) == (0, 0, 0) or (r, g, b) == (255, 255, 255):
        return -3.0 
    if r > 200 and g < 100 and b < 100: return 5.0
    if r > 200 and g > 150 and b < 100: return 3.0
    if r < 100 and g > 200 and b < 100: return 0.5
    if r < 100 and g < 200 and b > 200: return -1.0
    if r < 100 and g < 100 and b > 150: return -2.0
    return -3.0

# ==========================================
# エンドポイント: メイン画面 (警報情報)
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def get_alerts_info(request: Request):
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 地域コードの取得
        area_res = await client.get("https://www.jma.go.jp/bosai/common/const/area.json")
        areas = area_res.json() if area_res.status_code == 200 else {}
        
        # 1. 氾濫警報データの取得
        flood_res = await client.get("https://www.jma.go.jp/bosai/flood/data/r8/flood_xml.json")
        floods = flood_res.json() if flood_res.status_code == 200 else []
        
        flood_alerts = []
        if floods:
            floods.sort(key=lambda x: int(x.get('item', {}).get('code', ['0'])[0]), reverse=True)
            for f in floods:
                office_names = [areas.get('offices', {}).get(code, {}).get('name', '') for code in f.get('officeCodes', [])]
                class20_names = [areas.get('class20s', {}).get(code, {}).get('name', '') for code in f.get('class20Codes', [])]
                raw_time = f.get('reportDatetime', '')
                
                flood_alerts.append({
                    'offices': " ".join(office_names),
                    'targetName': f.get('riverName', ''),
                    'url': f"https://www.jma.go.jp/bosai/flood/data/r8/pdf/{f.get('pdfFilename', '')}",
                    'warnName': f.get('item', {}).get('name', ''),
                    'warnLevel': f.get('item', {}).get('code', ['0'])[0],
                    'time': raw_time.split("+")[0].replace("-", "/").replace("T", " ") if raw_time else "",
                    'publishingOffice': f.get('publishingOffice', '') + " 共同発表",
                    'targetCities': " ".join(class20_names)
                })

        # 2. 大雨警報データの取得
        rain_res = await client.get("https://www.jma.go.jp/bosai/warning/data/r8/map.json")
        rain_data_list = rain_res.json() if rain_res.status_code == 200 else []
        
        rain_alerts = []
        for office_data in rain_data_list:
            office_name = office_data.get('publishingOffice', '気象台')
            report_datetime = office_data.get('reportDatetime', '')
            time_str = report_datetime.split("+")[0].replace("-", "/").replace("T", " ") if report_datetime else ""
            
            warn_cities_lv5 = []
            warn_cities_lv3 = []
            warn_cities_lv2 = []
            
            class20_items = office_data.get('warning', {}).get('class20Items', [])
            for area in class20_items:
                city_code = area.get('areaCode')
                city_name = areas.get('class20s', {}).get(city_code, {}).get('name', '不明')
                
                for kind in area.get('kinds', []):
                    w_code = str(kind.get('code', '')).zfill(2)
                    status = kind.get('status', '')
                    if status != '解除' and status != '発表警報・注意報はなし':
                        if w_code in ['32', '33']:
                            if city_name not in warn_cities_lv5: warn_cities_lv5.append(city_name)
                        elif w_code == '03':
                            if city_name not in warn_cities_lv3: warn_cities_lv3.append(city_name)
                        elif w_code == '10':
                            if city_name not in warn_cities_lv2: warn_cities_lv2.append(city_name)
            
            if warn_cities_lv5:
                rain_alerts.append({
                    'offices': office_name,
                    'targetName': "特別警報 対象地域",
                    'url': "https://www.jma.go.jp/bosai/warning/",
                    'warnName': "大雨特別警報",
                    'warnLevel': "5",
                    'time': time_str,
                    'publishingOffice': office_name,
                    'targetCities': " ".join(warn_cities_lv5)
                })
            if warn_cities_lv3:
                rain_alerts.append({
                    'offices': office_name,
                    'targetName': "警報 対象地域",
                    'url': "https://www.jma.go.jp/bosai/warning/",
                    'warnName': "大雨警報",
                    'warnLevel': "3",
                    'time': time_str,
                    'publishingOffice': office_name,
                    'targetCities': " ".join(warn_cities_lv3)
                })
            if warn_cities_lv2:
                rain_alerts.append({
                    'offices': office_name,
                    'targetName': "注意報 対象地域",
                    'url': "https://www.jma.go.jp/bosai/warning/",
                    'warnName': "大雨注意報",
                    'warnLevel': "2",
                    'time': time_str,
                    'publishingOffice': office_name,
                    'targetCities': " ".join(warn_cities_lv2)
                })
                
        rain_alerts.sort(key=lambda x: int(x['warnLevel']), reverse=True)
            
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"flood_alerts": flood_alerts, "rain_alerts": rain_alerts}
    )

# ==========================================
# エンドポイント: 強震モニタAPI[cite: 3]
# ==========================================
@app.get("/api/realtime")
async def get_realtime_data():
    img_data = None
    img_url = ""
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        time_url = "http://www.kmoni.bosai.go.jp/webservice/server/pros/latest.json"
        req_time = urllib.request.Request(time_url, headers=headers)
        
        with urllib.request.urlopen(req_time, timeout=5, context=ctx) as res:
            latest_data = json.loads(res.read().decode('utf-8'))
            latest_time_str = latest_data.get("latest_time")
            
        if latest_time_str:
            dt = datetime.strptime(latest_time_str, "%Y/%m/%d %H:%M:%S")
            date_str = dt.strftime("%Y%m%d")
            time_str = dt.strftime("%Y%m%d%H%M%S")
            
            img_url = f"http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/jma_s/{date_str}/{time_str}.jma_s.gif"
            req_img = urllib.request.Request(img_url, headers=headers)
            
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req_img, timeout=5, context=ctx) as res_img:
                        img_data = res_img.read()
                        break 
                except Exception as e:
                    if attempt == 2:
                        print(f"[{time_str}] 取得エラー(リトライ失敗): {e} -> {img_url}")
                    else:
                        await asyncio.sleep(0.5)
                
    except Exception as e:
        print(f"時刻取得エラー: {e}")

    data = []
    if img_data:
        img = Image.open(BytesIO(img_data)).convert("RGB")
        for pt in OBSERVATION_POINTS:
            x, y = latlng_to_pixel(pt["lat"], pt["lng"])
            if 0 <= x < img.width and 0 <= y < img.height:
                r, g, b = img.getpixel((x, y))
                shindo = color_to_shindo(r, g, b)
            else:
                shindo = -3.0
            
            data.append({
                "id": pt["id"],
                "name": pt["name"],
                "lat": pt["lat"],
                "lng": pt["lng"],
                "shindo": shindo
            })

    return {"status": "ok", "data": data}