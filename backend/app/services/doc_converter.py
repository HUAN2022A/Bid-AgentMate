"""老式 .doc → .docx 转换：soffice 优先（Linux 部署），Word COM 兜底（Windows 开发机）。

116MB 级真实投标文件实测可行路径是 Word COM；两条链都无头运行：
- soffice --headless --convert-to docx（LibreOffice，装了就用）
- Word COM：DispatchEx 独立实例 + DisplayAlerts=None + 禁宏，try/finally 保证 Quit
"""
import shutil
import subprocess
from pathlib import Path

_SOFFICE_CANDIDATES = [
    "soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


class DocConvertError(Exception):
    """转换失败（附用户可读的处理建议）。"""


def _find_soffice() -> str | None:
    for cand in _SOFFICE_CANDIDATES:
        if Path(cand).exists() or shutil.which(cand):
            return cand
    return None


def _convert_by_soffice(src: str, out: str, soffice: str) -> None:
    # outdir 模式：soffice 自己按原名换扩展名落盘，完成后挪到目标路径
    outdir = Path(out).parent / "_soffice_tmp"
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", str(outdir), src],
            capture_output=True, timeout=600,
        )
        produced = outdir / (Path(src).stem + ".docx")
        if proc.returncode != 0 or not produced.exists():
            raise DocConvertError(f"soffice 转换失败: {proc.stderr.decode(errors='ignore')[:500]}")
        produced.replace(out)
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def _convert_by_word_com(src: str, out: str) -> None:
    try:
        import win32com.client  # noqa: PLC0415
        import pythoncom
    except ImportError as e:
        raise DocConvertError(
            "服务器未安装 LibreOffice，且 pywin32 不可用：请将 .doc 另存为 .docx 后重新上传"
        ) from e

    pythoncom.CoInitialize()
    word = None
    try:
        # DispatchEx 独立实例，不碰用户正在开的 Word 窗口
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0  # wdAlertsNone：禁止任何弹窗（缺字体/修复提示全屏蔽）
        try:
            word.AutomationSecurity = 3  # msoAutomationSecurityForceDisable：禁宏
        except Exception:  # noqa: BLE001 旧版本 Word 无此属性
            pass
        doc = word.Documents.Open(
            str(Path(src).resolve()), ReadOnly=True, AddToRecentFiles=False,
            ConfirmConversions=False, Visible=False,
        )
        try:
            doc.SaveAs2(str(Path(out).resolve()), FileFormat=16)  # 16 = wdFormatXMLDocument
        finally:
            doc.Close(False)
    except DocConvertError:
        raise
    except Exception as e:  # noqa: BLE001 COM 错误码五花八门，统一转可读信息
        raise DocConvertError(f"Word 转换失败（{e}）：可尝试手动另存为 .docx 后重新上传") from e
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:  # noqa: BLE001 Quit 失败不掩盖原错误
                pass
        pythoncom.CoUninitialize()


def convert_doc_to_docx(src_abs: str, out_abs: str) -> str:
    """把 .doc 转成 .docx 写到 out_abs，返回 out_abs。失败抛 DocConvertError。"""
    if Path(src_abs).suffix.lower() != ".doc":
        raise DocConvertError(f"仅处理 .doc，收到 {Path(src_abs).suffix}")
    Path(out_abs).parent.mkdir(parents=True, exist_ok=True)

    soffice = _find_soffice()
    if soffice:
        try:
            _convert_by_soffice(src_abs, out_abs, soffice)
            return out_abs
        except DocConvertError:
            pass  # soffice 失败继续试 COM（116MB 大文件 soffice 偶发超时）

    _convert_by_word_com(src_abs, out_abs)
    return out_abs
