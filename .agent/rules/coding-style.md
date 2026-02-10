---
trigger: always_on
---

1. 核心哲学 (Core Philosophy)
* Pythonic: 遵循 PEP 8 规范，利用 Python 的特性（如：列表推导式、上下文管理器 with、装饰器）编写简洁代码。

* 可读性优先: 代码是写给人看的，只是顺便给机器运行。变量名应具有描述性（例如：使用 is_stealth_mode_enabled 而非 st_md）。

* 积极封装: 一个函数只做一件事。如果一个函数的逻辑超过 60 行，应考虑拆分。

2. 路径处理与跨平台兼容
* 禁止使用字符串拼接路径: 严禁使用 path + "/data"。

* 强制使用 pathlib: 利用 pathlib.Path 对象处理所有文件系统操作，以自动处理 Windows (\) 和 macOS/Linux (/) 的路径差异。

Python
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "raw_markdown"
* 跨平台依赖: 优先选择纯 Python 实现或提供多平台 Wheel 的库。避免使用特定于 OS 的系统命令（如 os.system('ls')），改用 Python 标准库函数。

3. 数据模型与类型安全
* 强类型声明: 所有函数签名必须包含类型提示 (Type Hints)。

Python
def fetch_page_content(url: str, timeout: int = 30) -> str:
Pydantic 驱动:

* 配置管理: 使用 pydantic-settings 管理 .env 环境变量。

* 数据结构: 所有从网页提取的非结构化数据，必须通过 Pydantic Model 转化为结构化对象。

* 自动验证: 利用 Pydantic 的自动校验机制，确保进入数据库的数据 100% 符合预期。

4. 自动化与爬虫专项守则
* 隐身优先 (Stealth First): 所有浏览器操作必须通过 playwright-extra 的 stealth 插件。

* 防御性爬取:

* 延迟: 在点击和跳转之间加入随机的 human_delay (0.5s - 2s)。

* 重试机制: 对于网络波动，使用 tenacity 等库实现指数退避重试。

* 资源释放: 始终使用 async with 或 try...finally 确保浏览器实例和页面上下文在任务结束或崩溃时被正确关闭。

5. 错误处理与日志
* 禁止使用 print(): 全局使用内置 logging 模块。

* INFO: 记录任务进度（如：“开始抓取 HKU”）。

* WARNING: 记录非致命问题（如：“页面加载缓慢，正在重试”）。

* ERROR: 记录导致单个任务失败的问题。

* 自定义异常: 在 src/core/exceptions.py 中定义业务相关的异常（如 AdmissionScraperError, DataValidationError），不要通篇抛出通用的 Exception。

6. LLM 交互规范
* 提示词解耦: 不要将长段的 Prompt 硬编码在业务逻辑中。应将其存放在 src/agents/prompts/ 目录下的 .txt 或 .yaml 文件中。

* Token 优化: 在将 HTML 送入 LLM 前，必须先通过解析器（如 Crawl4AI）转换为 Markdown，剔除无用的 Scripts 和 CSS。

* 结构化输出: 强制 LLM 使用 JSON Mode，并配合 Pydantic 的 model_json_schema() 告诉 LLM 你需要的精确格式。

7. 目录与工程化
* 模块化导入: 使用绝对导入（例如 from src.core.config import settings），避免相对导入带来的路径混乱。

* 环境隔离: 永远在 venv 中运行。依赖项分为 requirements.txt (生产) 和 requirements-dev.txt (开发/测试)。

8. 测试与持续集成 (Testing & CI/CD)
* 函数长度限制: 严格遵守“单一职责原则”。单个函数逻辑代码建议不超过 60 行。超过此长度必须进行拆分封装，并为拆分后的核心逻辑编写单元测试。

* 测试框架: 统一使用 pytest。