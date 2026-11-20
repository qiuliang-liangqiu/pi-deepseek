"""跑分工具箱

模块划分（按"一次做题"的生命周期组织，便于单独替换）：
    api        平台 API 客户端（start/close/submit/hint/list），负责 409/duplicate/重试
    net        HTTP 原语（会话、cookie、原始请求、并发）
    recon      指纹识别，把"看到的东西"归到一个打法族
    payloads   各类漏洞的载荷库（XSS/SSTI/LFI/JWT/...）
    playbooks  指纹 -> 自动化尝试链，返回 flag 或 None
    race       竞态编排器（多路错峰），用于 TOCTOU / 限时窗口类漏洞
    governance 可执行纪律：SKIP / 看门狗 / 双地址探活 / VPN 预检
    cli        命令行入口

结论纪律（踩过坑）：
    * "不可解"必须本人用最小实验证一次；概率型失败先改并发模型
    * 探测用 Response.brief() 截断输出
    * subagent 用独立 work/<code> 目录，只回流结论与脚本
"""

__all__ = ["api", "net", "recon", "payloads", "playbooks", "race", "governance", "cli"]
__version__ = "1.0"
