# -*- coding: utf-8 -*-
"""本地 mock LLM（bigmodel 兼容）：支持 SSE 流式分块，用于无真实密钥时演示 Copilot 流式 UX。

- POST /api/paas/v4/chat/completions
  - body.stream=true  → SSE chunked：delta.content 分块推送（每块间隔 CHUNK_DELAY_MS）
  - body.stream 缺省 → 普通 JSON choices[0].message.content
"""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 对齐评分点动作的演示文案（不含报价类词、不含 [待补]）
TEXT = (
    "本节围绕评分点要求逐项组织实施方案：在总体组织方面，成立由项目经理牵头的专项实施组，"
    "明确各岗位职责与协同机制，确保全过程责任到人；在进度安排方面，"
    "按「准备—实施—调试—验收」四阶段推进，各阶段设置里程碑节点与交付物清单，"
    "进度偏差超过三天即启动纠偏预案；在质量保障方面，执行公司质量管理体系文件，"
    "关键工序实行自检、互检、专检三检制度，并留存完整过程记录备查；"
    "在安全文明施工方面，落实现场安全交底与隐患排查双重机制，确保施工全程零事故；"
    "在培训与售后方面，提供不少于两轮的操作培训，并建立 7×24 小时响应机制，"
    "重大问题四小时内到场处置。上述安排与评分细则逐条对应，可为评审核查提供完整依据。"
)
CHUNK_SIZE = 14
CHUNK_DELAY_MS = 280


def split_chunks(text: str) -> list[str]:
    return [text[i : i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]


class Handler(BaseHTTPRequestHandler):
    def _sse_chunk(self, obj: dict) -> bytes:
        return b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        stream = body.get("stream", False)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
        if stream:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        if not stream:
            payload = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": TEXT}}]},
                ensure_ascii=False,
            ).encode("utf-8")
            self.wfile.write(payload + b"\n")
            return

        def write(data: bytes):
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        for piece in split_chunks(TEXT):
            write(self._sse_chunk({"choices": [{"delta": {"content": piece}}]}))
            time.sleep(CHUNK_DELAY_MS / 1000)
        write(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def log_message(self, fmt, *args):
        print("[mock]", fmt % args, flush=True)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 8788), Handler)
    print("mock llm listening on 127.0.0.1:8788", flush=True)
    srv.serve_forever()
