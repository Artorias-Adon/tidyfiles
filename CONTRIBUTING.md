# Contributing to TidyFiles

Thanks for taking the time to improve TidyFiles. 中文贡献说明在下方。

## Development setup

TidyFiles has **no third-party runtime dependencies** – only Python 3.10+ and
Tkinter. Clone the repository and run the app directly:

```bash
git clone https://github.com/Artorias-Adon/tidyfiles.git
cd tidyfiles
python main.py
```

Run the test suite before opening a pull request:

```bash
python -m unittest discover -s tests -v
```

On Linux, install Tkinter first if it is missing (`sudo apt install python3-tk`
on Debian/Ubuntu).

## Pull requests

- Keep the core engine (`tidyfiles/organizer.py`) independent from the UI.
- Add or update tests for behaviour changes.
- Keep the interface usable at the default 1120 × 820 window size.
- For new file extensions, prefer adding them to the existing category table in
  `tidyfiles/categories.py` rather than creating a new near-duplicate category.
- Include a screenshot for visible UI changes.

## 中文说明

欢迎提交 Issue 和 Pull Request：

1. Fork 本仓库并创建功能分支。
2. 运行 `python -m unittest discover -s tests -v`，确保现有 30+ 项测试通过。
3. 新功能请补充测试；界面改动请附截图。
4. 提交 PR 时说明改动目的、使用方式和测试结果。

请不要在代码或示例中提交真实的个人文件夹路径、账号令牌或隐私文件。
