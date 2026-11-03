cd /opt/xspawner    # 换成你的 WorkingDirectory（systemctl cat 里看）

python3 - <<'PY' > admpw.txt
import secrets, hashlib

pw    = 'YOUR-PASSWORD'      # ← 改成你的口令
iters = 200_000               # 迭代次数，200000 约 50~100ms/次
salt  = secrets.token_bytes(16)

dk = hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), salt, iters)
print(f"pbkdf2_sha256${iters}${salt.hex()}${dk.hex()}")
PY

chmod 600 admpw.txt
cat admpw.txt