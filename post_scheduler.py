# Publicador automático para X (3 cuentas, imágenes y ALT). Hora America/Montevideo.
import csv, os, json, mimetypes, requests, datetime as dt
from zoneinfo import ZoneInfo

X_API = "https://api.x.com/2"
UPLOAD_V2 = f"{X_API}/media/upload"
ALT_V11  = "https://upload.twitter.com/1.1/media/metadata/create.json"
TZ = ZoneInfo("America/Montevideo")

ACCOUNTS = json.loads(os.getenv("ACCOUNTS_JSON", '{"ACC1":"es","ACC2":"en","ACC3":"de"}'))
CLIENT_ID = os.getenv("X_CLIENT_ID")
CSV_FILE = os.getenv("CSV_FILE","calendar.csv")
STATE_FILE = os.getenv("STATE_FILE","posted.csv")
WINDOW_MIN = int(os.getenv("WINDOW_MIN","5"))

def now_utc(): return dt.datetime.now(dt.timezone.utc)

def parse_row(row):
    if not row or row["fecha"].strip().startswith("#"): return None
    t_local = dt.datetime.fromisoformat(f'{row["fecha"].strip()} {row["hora_MVD"].strip()}:00').replace(tzinfo=TZ)
    return {
        "when_utc": t_local.astimezone(dt.timezone.utc),
        "image": row["imagen"].strip(),
        "alt": {"es": row["alt_es"].strip(), "en": row["alt_en"].strip(), "de": row["alt_de"].strip()},
        "text": {"es": row["texto_es"].strip(), "en": row["texto_en"].strip(), "de": row["texto_de"].strip()},
        "key": f'{row["fecha"]}_{row["hora_MVD"]}_{row["texto_es"][:20]}'
    }

def due(now, when):
    delta = (now - when).total_seconds()/60.0
    return 0 <= delta <= WINDOW_MIN

def refresh_access_token(refresh_token):
    data = {"grant_type":"refresh_token","refresh_token":refresh_token,"client_id":CLIENT_ID}
    r = requests.post(f"{X_API}/oauth2/token",
                      headers={"Content-Type":"application/x-www-form-urlencoded"}, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]

def get_bytes(path_or_url):
    if not path_or_url: return None, None
    if path_or_url.startswith("http"):
        r = requests.get(path_or_url, timeout=30); r.raise_for_status()
        return r.content, r.headers.get("content-type","image/jpeg")
    data = open(path_or_url,"rb").read()
    return data, (mimetypes.guess_type(path_or_url)[0] or "image/jpeg")

def upload_media(token, data, content_type):
    h = {"Authorization": f"Bearer {token}"}
    init = requests.post(UPLOAD_V2, headers=h, files={
        "command":(None,"INIT"), "media_type":(None,content_type),
        "total_bytes":(None,str(len(data))), "media_category":(None,"tweet_image")})
    init.raise_for_status(); media_id = init.json()["data"]["id"]
    app = requests.post(UPLOAD_V2, headers=h, files={
        "command":(None,"APPEND"), "media_id":(None,media_id),
        "segment_index":(None,"0"), "media":("file", data, content_type)})
    app.raise_for_status()
    fin = requests.post(UPLOAD_V2, headers=h, files={"command":(None,"FINALIZE"), "media_id":(None,media_id)})
    fin.raise_for_status(); return media_id

def set_alt_text(token, media_id, alt_text):
    if not alt_text: return
    payload = {"media_id": media_id, "alt_text": {"text": alt_text[:1000]}}
    requests.post(ALT_V11, headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
                  data=json.dumps(payload))

def post_tweet(token, text, media_id=None):
    import requests, json
    payload = {"text": text}
    if media_id: payload["media"] = {"media_ids":[media_id]}
    r = requests.post(f"{X_API}/tweets",
        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
        data=json.dumps(payload))
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        print("ERROR POST:", r.status_code, r.text)
        raise
    return r.json()["data"]["id"]

def load_state():
    if not os.path.exists(STATE_FILE): return set()
    return set(open(STATE_FILE, encoding="utf-8").read().splitlines())

def save_state(keys):
    with open(STATE_FILE,"a",encoding="utf-8") as f:
        for k in keys: f.write(k+"\n")

def main():
    now = now_utc(); posted = load_state(); due_rows=[]
    rows = list(csv.DictReader(open(CSV_FILE, encoding="utf-8")))
    for r in rows:
        item = parse_row(r)
        if not item or item["key"] in posted: continue
        if due(now, item["when_utc"]): due_rows.append(item)
    if not due_rows: print("Nada para publicar ahora."); return

    just=[]
    for item in due_rows:
        for acc, lang in ACCOUNTS.items():
            rt = os.getenv(f"REFRESH_TOKEN_{acc}")
            if not rt: 
                print(f"[{acc}] sin REFRESH_TOKEN_*, salto."); 
                continue
            token = refresh_access_token(rt)
            media_id = None
            if item["image"]:
                data, ct = get_bytes(item["image"])
                if data:
                    try:
                        media_id = upload_media(token, data, ct)
                        set_alt_text(token, media_id, item["alt"].get(lang,""))
                    except Exception as e:
                        print(f"[{acc}] no pude subir imagen: {e}. Publico solo texto.")
            tid = post_tweet(token, item["text"][lang], media_id)
            print(f"[{acc}] publicado {tid}")
        just.append(item["key"])
    save_state(just)

if __name__ == "__main__":
    main()
