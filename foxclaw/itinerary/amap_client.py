"""
Foxclaw 行程子系统 — 高德地图适配层
封装高德 Web Service API。无 API Key 时所有方法降级返回 None,
行程内核自动回退到纯算法估算(直线距离)。
本地配置环境变量 AMAP_MAPS_API_KEY 后即接入真实高德数据。

对齐高德 Web Service API 参数格式:
  - 坐标:经度在前,纬度在后,逗号分隔 "116.397,39.909"
  - geocode / regeo / route(walking|driving|transit|cycling) / poi / weather

也可改为接官方 MCP server: @amap/amap-maps-mcp-server
(通过 npx 启动,环境变量同样是 AMAP_MAPS_API_KEY)。
"""

import os
import json
from typing import Optional
from urllib import request, parse


AMAP_BASE = "https://restapi.amap.com/v3"


class AMapClient:
    """高德 Web Service 客户端。无 Key 则 enabled=False,调用返回 None。"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("AMAP_MAPS_API_KEY")
        self.enabled = bool(self.api_key)

    def _get(self, path: str, params: dict) -> Optional[dict]:
        if not self.enabled:
            return None
        params["key"] = self.api_key
        url = f"{AMAP_BASE}/{path}?{parse.urlencode(params)}"
        try:
            with request.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data if data.get("status") == "1" else None
        except Exception:
            return None

    # ── 地理编码 ──────────────────────────────────────────
    def geocode(self, address: str, city: str = "") -> Optional[tuple]:
        """地址 → (lng, lat)。"""
        data = self._get("geocode/geo", {"address": address, "city": city})
        if data and data.get("geocodes"):
            loc = data["geocodes"][0]["location"]      # "lng,lat"
            lng, lat = loc.split(",")
            return float(lng), float(lat)
        return None

    def regeo(self, lng: float, lat: float) -> Optional[str]:
        """坐标 → 地址。"""
        data = self._get("geocode/regeo", {"location": f"{lng},{lat}"})
        if data and data.get("regeocode"):
            return data["regeocode"].get("formatted_address")
        return None

    # ── 路线规划 ──────────────────────────────────────────
    def route(self, origin: str, destination: str, mode: str = "driving",
              city: str = "") -> Optional[dict]:
        """真实路线。origin/destination 为 'lng,lat'。
        返回 {distance_km, duration_min}。"""
        path_map = {
            "driving": "direction/driving",
            "walking": "direction/walking",
            "cycling": "direction/bicycling",
            "transit": "direction/transit/integrated",
        }
        path = path_map.get(mode, "direction/driving")
        params = {"origin": origin, "destination": destination}
        if mode == "transit":
            params["city"] = city
        data = self._get(path, params)
        if not data:
            return None
        try:
            route = data["route"]
            if mode == "transit":
                seg = route["transits"][0]
                return {"distance_km": round(int(seg["distance"]) / 1000, 2),
                        "duration_min": round(int(seg["duration"]) / 60, 1),
                        "source": "amap"}
            else:
                path0 = route["paths"][0]
                return {"distance_km": round(int(path0["distance"]) / 1000, 2),
                        "duration_min": round(int(path0["duration"]) / 60, 1),
                        "source": "amap"}
        except Exception:
            return None

    # ── POI / 周边 / 天气 ─────────────────────────────────
    def search_poi(self, keywords: str, city: str = "", offset: int = 10) -> Optional[list]:
        data = self._get("place/text",
                         {"keywords": keywords, "city": city, "offset": offset})
        if data and data.get("pois"):
            return [{"name": p["name"], "address": p.get("address", ""),
                     "location": p["location"], "type": p.get("type", "")}
                    for p in data["pois"]]
        return None

    def nearby(self, location: str, keywords: str = "", radius: int = 1000) -> Optional[list]:
        data = self._get("place/around",
                         {"location": location, "keywords": keywords, "radius": radius})
        if data and data.get("pois"):
            return [{"name": p["name"], "address": p.get("address", ""),
                     "location": p["location"]} for p in data["pois"]]
        return None

    def weather(self, city: str) -> Optional[dict]:
        data = self._get("weather/weatherInfo", {"city": city})
        if data and data.get("lives"):
            w = data["lives"][0]
            return {"city": w["city"], "weather": w["weather"],
                    "temperature": w["temperature"], "wind": w["winddirection"]}
        return None
