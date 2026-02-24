# salute/jazz.py
import aiohttp
import asyncio
import base64
import datetime
import json
import jwt
from jwt import PyJWK
import uuid
from typing import List, Optional, Dict, Any
from functools import wraps
import time
from config import SDK_KEY_ENCODED

class SaluteJazzAPI:
    def __init__(self, sdk_key_encoded: str):
        self.sdk_key_encoded = sdk_key_encoded
        sdk_key = json.loads(base64.b64decode(sdk_key_encoded))
        self.project_id = sdk_key['projectId']
        self.jwk = PyJWK.from_dict(sdk_key['key'], algorithm='ES384')
        self.access_token = None
        self.token_expires = 0

    async def _get_access_token(self) -> str:
        if self.access_token and time.time() < self.token_expires:
            return self.access_token

        # Генерация транспортного токена
        jwt_header = {
            'typ': 'JWT',
            "alg": 'ES384',
            'kid': self.jwk.key_id,
        }
        iat = datetime.datetime.utcnow()
        exp = iat + datetime.timedelta(hours=1)
        jti = str(uuid.uuid4())
        jwt_payload = {
            "iat": iat,
            "exp": exp,
            "jti": jti,
            "sdkProjectId": self.project_id,
            "sub": '15eca6c5-fb2d-48f2-804a-f97e542ebd33',
        }
        transport_token = jwt.encode(
            headers=jwt_header, 
            payload=jwt_payload, 
            key=self.jwk.key, 
            algorithm='ES384'
        )

        # Получение access token
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.salutejazz.ru/v1/auth/login",
                headers={
                    'Accept': 'application/json',
                    'Authorization': f'Bearer {transport_token}'
                }
            ) as response:
                data = await response.json()
                self.access_token = data['token']
                self.token_expires = time.time() + 3600  # 1 час
                return self.access_token

    async def create_room(self, room_title: str) -> Dict[str, Any]:
        token = await self._get_access_token()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.salutejazz.ru/v1/room/create",
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'Authorization': f'Bearer {token}'
                },
                json={
                    "roomTitle": room_title,
                    "serverVideoRecordAutoStartEnabled": False,
                    "roomType": "MEETING",
                    "transcriptionAutoStartEnabled": True,
                    "summarizationEnabled": False
                }
            ) as response:
                return await response.json()
                
    async def disable_transcription(self, room_id: str) -> bool:
        token = await self._get_access_token()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.salutejazz.ru/v1/room/{room_id}/settings/update",
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'Authorization': f'Bearer {token}'
                },
                json={
                    "transcriptionAutoStartEnabled": False
                }
            ) as response:
                return response.status == 204

    async def disable_room(self, room_id: str) -> bool:
        token = await self._get_access_token()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.salutejazz.ru/v1/room/{room_id}/disable",
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'Authorization': f'Bearer {token}'
                },
                json={}
            ) as response:
                return response.status == 204

    async def get_transcriptions(self, room_id: str) -> Dict[str, Any]:
        token = await self._get_access_token()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.salutejazz.ru/v1/room/{room_id}/transcriptions",
                headers={
                    'Accept': 'application/json',
                    'Authorization': f'Bearer {token}'
                }
            ) as response:
                return await response.json()
                
api = SaluteJazzAPI(SDK_KEY_ENCODED)

# Функция для логирования транскрипций
def log_transcription(room_id: str, transcription_data: str, parsed_text: str = None):
    """Логирует транскрипции в файл"""
    try:
        import time
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"""
=== Transcription - {timestamp} ===
Room ID: {room_id}
Raw Data: {transcription_data}
{"="*60}

"""
        if parsed_text:
            log_entry += f"""
=== Parsed Transcription - {timestamp} ===
Room ID: {room_id}
Parsed Text: {parsed_text}
{"="*60}

"""
        
        with open("transcriptions_log.txt", "a", encoding="utf-8") as f:
            f.write(log_entry)
        print(f"Транскрипция залогирована в transcriptions_log.txt")
    except Exception as e:
        print(f"Ошибка при логировании транскрипции: {e}")

async def create_rooms(count: int, save_path: str = "jazz_rooms.json"):
    rooms = {}
    
    for i in range(count):
        room_data = await api.create_room(f"Room #{i+1}")
        room_id = room_data['roomId']
        room_url = room_data['roomUrl']
        rooms[f"Room #{i+1}"] = room_url
        
        # Задержка между запросами чтобы избежать лимитов
        await asyncio.sleep(1)
    
    with open(save_path, 'w') as f:
        json.dump(rooms, f, indent=2)
    
    return rooms

async def get_room_transcription(room_url: str) -> str:
    room_id = room_url.split('/')[-1].split('?')[0]
    transcriptions = await api.get_transcriptions(room_id)
    result_json = json.dumps(transcriptions, ensure_ascii=False)
    
    # Логируем сырые данные транскрипции
    log_transcription(room_id, result_json)
    
    return result_json

def parse_transcriptions(
    transcriptions_json: str, 
    known_names: List[str],
    start_time: Optional[datetime.datetime] = None,
    end_time: Optional[datetime.datetime] = None
) -> str:
    data = json.loads(transcriptions_json)
    transcriptions = data.get('transcriptions', [])
    
    filtered = []
    for t in transcriptions:
        if t.get('participantName') not in known_names:
            continue
            
        if start_time or end_time:
            # createdAt приходит в UTC (например, 2025-10-07T14:24:40Z)
            created_at_utc = datetime.datetime.fromisoformat(
                t['createdAt'].replace('Z', '+00:00')
            )

            # Преобразуем московское время в UTC для сравнения
            msk_timezone = datetime.timezone(datetime.timedelta(hours=3))
            
            if start_time:
                # Если start_time без часового пояса, считаем его московским временем
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=msk_timezone)
                # Конвертируем в UTC для сравнения
                start_time_utc = start_time.astimezone(datetime.timezone.utc)
            else:
                start_time_utc = None
                
            if end_time:
                # Если end_time без часового пояса, считаем его московским временем
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=msk_timezone)
                # Конвертируем в UTC для сравнения
                end_time_utc = end_time.astimezone(datetime.timezone.utc)
            else:
                end_time_utc = None

            # Сравниваем время в UTC
            if start_time_utc and created_at_utc < start_time_utc:
                continue
            if end_time_utc and created_at_utc > end_time_utc:
                continue
                
        filtered.append(t)
    
    dialog_lines = []
    for entry in filtered:
        name = entry.get('participantName', 'Неизвестный')
        text = entry.get('text', '')
        dialog_lines.append(f"{name}: {text}")
    
    parsed_text = "\n".join(dialog_lines)
    
    # Логируем распарсенный текст
    room_id = "unknown"
    if data.get('roomId'):
        room_id = data['roomId']
    log_transcription(room_id, transcriptions_json, parsed_text)
    
    return parsed_text
