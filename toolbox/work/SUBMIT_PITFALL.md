# 平台坑（2026-11 实测，worker B）
附件型「离线」题也必须 `start` 该 unique_code，否则 `submit` 会返回 correct=False（假阴性）；
start 后再提交同一 flag 就 correct=True。容器地址是共享的 nginx（只发附件 zip），
所以 start 只是为这道题建会话，不需要真的打容器；做完记得 close 释放名额（上限 3）。
另外出现过「先返回 correct=False，随后同样字符串报 duplicate=True（已计分）」的异步计分抖动，
连错两次仍可疑时，先 start 再重提一次同 flag 比换 flag 更划算。
