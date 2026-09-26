# 米国マクロ指標トラッカー

FREDのデータで米国経済を「5つの問い」に分けて毎日判定する、個人用のダッシュボード。
GitHub Actions が毎日 **日本時間 1:47** にデータを取り直し、GitHub Pages に公開する。
（前夜に米国で発表された指標を取り込める最も早い時刻。日次の金利は米国の前々日終値までになる）

| 問い | 主役（判定に使う） |
|---|---|
| 景気は拡大しているか | 実質GDP、鉱工業生産 |
| 雇用は崩れていないか | 雇用者数、サーム・ルール |
| インフレは2%に向かっているか | コアPCE、コアCPI |
| 金融環境は締まっているか | 実質金利、2年債 |
| 景気後退の予兆はあるか | 10年-3か月金利差、建築許可 |

補助指標（18本）と参考指標（3本：VIX・貿易収支・経常収支）は判定には使わず、確認用に表示する（合計31本）。

各指標には重要度（超重要・重要・中・低）を付けている。基準は「発表時に相場が動く大きさ＋FRBの政策への直結度」。

---

## はじめての設定（1回だけ・約10分）

1. **リポジトリを作る**
   GitHubで「New repository」→ 名前 `macro-dashboard` → **Public** を選んで作成
   （無料プランのGitHub PagesはPublicリポジトリが必要）

2. **ファイルをアップロードする**
   「uploading an existing file」→ zipを展開した中身を全部ドラッグ →「Commit changes」
   ※ `.github` フォルダは隠しフォルダ。Macは Finder で `Cmd + Shift + .` を押すと表示される。
   　アップロードされなかった場合は「Add file → Create new file」でファイル名に
   　`.github/workflows/update.yml` と入力し、中身を貼り付ける。

3. **FREDのAPIキーを登録する**
   Settings → Secrets and variables → Actions → 「New repository secret」
   Name: `FRED_API_KEY`／Secret: 自分のAPIキー

4. **Pagesを有効にする**
   Settings → Pages → Build and deployment の Source を **GitHub Actions** にする

5. **初回を手動で動かす**
   Actions タブ →「毎日データ更新」→「Run workflow」
   3〜5分で完了。ページは `https://acchan-iv.github.io/macro-dashboard/`

以降は毎朝自動で更新される。

---

## よく触る場所

| やりたいこと | 触るファイル |
|---|---|
| 判定の閾値を変える | `config/indicators.json` の `questions[].params` |
| 指標を追加・削除する | `config/indicators.json` の `indicators` に1ブロック足す／消す |
| 景気サイクル（4段階）を表示する | `config/indicators.json` の `settings.show_cycle` を `true` |
| 更新時刻を変える | `.github/workflows/update.yml` の `cron`（UTCで書く） |
| 重要度を変える | `config/indicators.json` の各指標の `tier`（S=超重要／A=重要／B=中／C=低） |
| 2つの指標の差を新しい指標にする | `derived` を書く（例：`cutpricing` ＝ 2年債−FF金利） |

## AI連携

`https://acchan-iv.github.io/macro-dashboard/data/summary.json` の `handoff_text` に、
その日の5つの判定・大きな変化・更新遅れ・次の発表予定が文章でまとまっている。
朝ルーティンやマクロ分析スキルからこのURLを読み込めば、手で写す必要がない。

## データの扱いについて

- **S&P500は入れていない**：S&P Dow Jones Indices が許可のない転載を禁止しているため（公開ページのため）。
- **ハイイールド債スプレッドも入れていない**：ICE社のデータで、FREDでは3年分のみ・第三者への公開は事前許可が必要なため。
- ミシガン大学指数とVIXは、FREDの表記に従って出典を明記している。ミシガン大学指数は提供元の要請でFREDでは1か月遅れ。
- ISM製造業指数は2016年にFREDから削除されたため、フィラデルフィア連銀の製造業指数で代用。

## 過去検証の注意

ページ下部の「過去の判定」は、各月末時点で発表済みだったはずのデータだけで判定している（発表までの日数は概算）。
ただし使っているのは後から改定された値なので、当時の速報値での判定とは一致しない場合がある。

## テスト（手元のPCで試す場合）

```
pip install -r requirements.txt
python tests/test_rules.py                         # 判定ルールのテスト（実データの値で検算）
python tests/make_fixtures.py tests/fixtures       # 偽データを作る
python scripts/build_data.py --offline tests/fixtures
cd docs && python -m http.server 8000              # http://localhost:8000 で確認（テストデータの表示が出る）
```

本番の取得を手元で試す場合は `FRED_API_KEY=xxxx python scripts/build_data.py`。

---

出典：FRED（セントルイス連邦準備銀行）ほか各統計の公表元。判定は設定ファイルのルールによる機械的な集計で、投資助言ではない。
