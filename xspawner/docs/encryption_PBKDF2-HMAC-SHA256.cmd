cd /opt/xspawner    # 换成你的 WorkingDirectory（systemctl cat 里看）

python3 - <<'PY' > admpw.txt
from xspawner.plugins.administrator.encryption import _make_pbkdf2

# _make_pbkdf2 只生成口令哈希，不写文件；这里把输出重定向到 admpw.txt
print(_make_pbkdf2('YOUR-PASSWORD'))      # ← 改成你的口令
PY

chmod 600 admpw.txt
cat admpw.txt
