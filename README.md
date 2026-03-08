# Lighter Portfolio Bot

Lighter DEX アカウント (`281474976622700`) のポートフォリオ変動を
定期的に Discord へ通知する Bot。GitHub Actions で動作。

## 構成
| ファイル | 役割 |
|----------|------|
| `bot.py` | Lighter API → 計算 → Discord 送信 |
| `.github/workflows/lighter-bot.yml` | 定期実行 (cron) |
| `deploy.sh` | ワンクリックデプロイ・設定変更 |

## 再設定
```bash
bash deploy.sh    # 間隔・モードを対話で選び直し→push
```

## 手動実行
GitHub → Actions → Lighter Portfolio Bot → Run workflow
