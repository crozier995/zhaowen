@echo off
cd /d "%~dp0"
echo 正在抓取今日新闻，请稍候...
python update.py
echo.
echo 更新完成！双击 index.html 即可阅读。
echo （如需 Kimi 的每日点评，在 Kimi Code 里说一句「更新新闻点评」即可）
pause
