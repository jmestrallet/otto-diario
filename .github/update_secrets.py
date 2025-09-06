# Actualiza Secrets de GitHub con refresh tokens nuevos (rotación automática)
import os, json, base64, requests
from nacl import encoding, public

GH_PAT = os.environ["GH_PAT"]
OWNER, REPO = os.environ["GH_REPO"].split("/")
API = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/secrets"

with open("new_refresh_tokens.json","r",encoding="utf-8") as f:
    data = json.load(f)   # {"ACC1":"<new_rt>", "ACC2":"<new_rt>", ...}

# Obtener public key de secrets
r = requests.get(f"{API}/public-key", headers={"Authorization": f"Bearer {GH_PAT}",
                                               "Accept":"application/vnd.github+json"})
r.raise_for_status()
pk = r.json()
key_id = pk["key_id"]
public_key = pk["key"]

def encrypt(public_key_b64, secret_value):
    pk_bytes = base64.b64decode(public_key_b64)
    sealed_box = public.SealedBox(public.PublicKey(pk_bytes))
    enc = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(enc).decode("utf-8")

for acc_alias, new_rt in data.items():
    secret_name = f"REFRESH_TOKEN_{acc_alias}"
    enc_value = encrypt(public_key, new_rt)
    payload = {"encrypted_value": enc_value, "key_id": key_id}
    resp = requests.put(f"{API}/{secret_name}",
                        headers={"Authorization": f"Bearer {GH_PAT}",
                                 "Accept":"application/vnd.github+json"},
                        json=payload)
    if resp.status_code not in (201, 204):
        print("FALLO ACTUALIZAR", secret_name, resp.status_code, resp.text)
    else:
        print("ACTUALIZADO", secret_name)
