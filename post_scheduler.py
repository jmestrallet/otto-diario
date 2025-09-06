# Publicador automático: media por v1.1 (OAuth1) si hay claves, tweet por v2 (OAuth2).
import csv, os, json, mimetypes, requests, datetime as dt
from zoneinfo import ZoneInfo
from requests_oauthlib import OAuth1

# ===== Constantes =====
X_API = "https://api.x.com/2"
UPLOAD_V2 = f"{X_API}/media/upload"
# v1.1 para media/ALT
UPLOAD_V11 = "https://upload.twitter.com/1.1/media/upload.json"
META_V11   = "https://upload.twitter.com/1.1/media/metadata/create.json"

TZ = ZoneInfo("America/Montevideo")

ACCOUNTS   = json.loads(os.getenv("ACCOUNTS_JSON", '{"ACC1":"es","ACC2":"en","ACC3":"de"}'))
CLIENT_ID  = os.getenv("X_CLIENT_ID")
CSV_FILE   = os.getenv("CSV_FILE","calendar.csv")
STATE_FILE = os.getenv("STATE_FILE","posted.csv")
WINDOW_MIN = int(os.getenv("WINDOW_MIN","5"))

NEW_REFRESH = {}  # refresh tokens NUEVOS por cuenta (para rotación)

# ===== Utilidades =====
def now_utc():
    return dt.datetime.now(dt.timezone.utc)

def parse_row(row):
    if not row or row["fecha"].strip().startswith("#"):
        return None
    t_local = dt.datetime.fromisoformat(f'{row["fecha"].strip()} {row["hora_MVD"].strip()}:00').replace(tzinfo=TZ)
    return {
        "when_utc": t_local.astimezone(dt.timezone.utc),
        "image": row["imagen"].strip(),
        "alt": {"es": row.get("alt_es","").strip(), "en": row.get("alt_en","").strip(), "de": row.get("alt_de","").strip()},
        "text": {"es": row.get("texto_es","").strip(), "en": row.get("texto_en","").strip(), "de": row.get("texto_de","").strip()},
        "key": f'{row["fecha"]}_{row["hora_MVD"]}_{row.get("texto_es","")[:20]}'
    }

def due(now, when):
    delta = (now - when).total_seconds()/60.0
    return 0 <= delta <= WINDOW_MIN

def get_bytes(path_or_url):
    if not path_or_url:
        return None, None
    if path_or_url.startswith("http"):
        r = requests.get(path_or_url, timeout=30)
        r.raise_for_status()
        return r.content, r.headers.get("content-type","image/jpeg")
    data = open(path_or_url,"rb").read()
    return data, (mimetypes.guess_type(path_or_url)[0] or "image/jpeg")

def load_state():
    if not os.path.exists(STATE_FILE):
        return set()
    return set(open(STATE_FILE, encoding="utf-8").read().splitlines())

def save_state(keys):
    with open(STATE_FILE,"a",encoding="utf-8") as f:
        for k in keys:
            f.write(k+"\n")

# ===== OAuth2 (v2) =====
def refresh_access_token(acc_alias, refresh_token):
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID}
    r = requests.post(f"{X_API}/oauth2/token",
                      headers={"Content-Type": "application/x-www-form-urlencoded"},
                      data=data, timeout=30)
    print("TOKEN STATUS:", r.status_code, r.text)
    r.raise_for_status()
    j = r.json()
    new_rt = j.get("refresh_token")
    if new_rt and new_rt != refresh_token:
        NEW_REFRESH[acc_alias] = new_rt
    print("SCOPES:", j.get("scope"))
    return j["access_token"]

def upload_media_v2(token, data, content_type):
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
    fin.raise_for_status()
    return media_id

def post_tweet_v2(token, text, media_id=None):
    payload = {"text": text}
    if media_id:
        payload["media"] = {"media_ids":[media_id]}
    r = requests.post(f"{X_API}/tweets",
        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
        data=json.dumps(payload), timeout=30)
    if r.status_code >= 400:
        print("POST ERROR:", r.status_code, r.text)
    r.raise_for_status()
    return r.json()["data"]["id"]

# ===== OAuth1 (solo para media/ALT) =====
def oauth1_client(acc_alias):
    ck = os.getenv("X_API_KEY")
    cs = os.getenv("X_API_SECRET")
    at = os.getenv(f"X_ACCESS_TOKEN_{acc_alias}")
    st = os.getenv(f"X_ACCESS_SECRET_{acc_alias}")
    if not (ck and cs and at and st):
        missing = [n for n,v in [
            ("X_API_KEY", ck), ("X_API_SECRET", cs),
            (f"X_ACCESS_TOKEN_{acc_alias}", at), (f"X_ACCESS_SECRET_{acc_alias}", st)
        ] if not v]
        print(f"[{acc_alias}] OAuth1 faltan vars: {', '.join(missing)}")
        return None
    return OAuth1(ck, cs, at, st)


def upload_media_oauth1(o1, data, content_type):
    files = {"media": ("file", data, content_type or "application/octet-stream")}
    r = requests.post(UPLOAD_V11, auth=o1, files=files, timeout=60)
    r.raise_for_status()
    return r.json()["media_id_string"]

def set_alt_text_oauth1(o1, media_id, alt_text):
    if not alt_text:
        return
    try:
        payload = {"media_id": media_id, "alt_text": {"text": alt_text[:1000]}}
        requests.post(META_V11, auth=o1, json=payload, timeout=30)
    except Exception as e:
        print("ALT skip:", e)

# ===== Main =====
def main():
    now = now_utc()
    posted = load_state()
    due_rows = []

    rows = list(csv.DictReader(open(CSV_FILE, encoding="utf-8")))
    for r in rows:
        item = parse_row(r)
        if not item or item["key"] in posted:
            continue
        if due(now, item["when_utc"]):
            due_rows.append(item)

    if not due_rows:
        print("Nada para publicar ahora.")
        return

    just = []
    for item in due_rows:
        for acc, lang in ACCOUNTS.items():
            rt = os.getenv(f"REFRESH_TOKEN_{acc}")
            if not rt:
                print(f"[{acc}] sin REFRESH_TOKEN_*, salto.")
                continue

            token = refresh_access_token(acc, rt)

            # Confirma user-context
            me = requests.get(f"{X_API}/users/me", headers={"Authorization": f"Bearer {token}"}, timeout=15)
            print(f"[{acc}] ME:", me.status_code, me.text[:200])

           media_id = None
if item["image"]:
    data, ct = get_bytes(item["image"])
    if data:
        o1 = oauth1_client(acc)
        try:
            if o1:
                media_id = upload_media_oauth1(o1, data, ct)
                set_alt_text_oauth1(o1, media_id, item["alt"].get(lang, ""))
            else:
                media_id = upload_media_v2(token, data, ct)
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            body = getattr(e.response, "text", "")[:200]
            print(f"[{acc}] v1.1 upload fallo ({code}): {body} — intento v2…")
            try:
                media_id = upload_media_v2(token, data, ct)
            except Exception as e2:
                print(f"[{acc}] v2 upload fallo: {e2}. Publico solo texto.")
        except Exception as e:
            print(f"[{acc}] no pude subir imagen: {e}. Publico solo texto.")


            tid = post_tweet_v2(token, item["text"][lang], media_id)
            print(f"[{acc}] publicado (oauth2) {tid}")

        just.append(item["key"])

    save_state(just)

    if NEW_REFRESH:
        with open("new_refresh_tokens.json","w",encoding="utf-8") as f:
            json.dump(NEW_REFRESH, f)
        print("ROTATED:", NEW_REFRESH)

if __name__ == "__main__":
    main()
