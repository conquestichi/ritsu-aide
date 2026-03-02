# マウスボタンテストガイド

## テストスクリプト

マウスサイドボタン（XButton1/XButton2）の認識をテストするためのスクリプトを3つ用意しました。

### 1. simple_mouse_test.py（推奨）
**最もシンプルで確実な実装**

```bash
python simple_mouse_test.py
```

- XButton1/XButton2のみを検出
- 押下（DOWN）と離した時（UP）を表示
- Ctrl+Cで終了

**期待される出力:**
```
Simple Mouse Hook Test
Press XButton1 or XButton2 (mouse side buttons)
Press Ctrl+C to exit

Hook installed (ID: 12345678)

[DOWN] XButton1
[UP] XButton1
[DOWN] XButton2
[UP] XButton2
```

### 2. debug_mouse_all.py
**すべてのマウスメッセージを詳細表示**

```bash
python debug_mouse_all.py
```

- すべてのマウスイベントを表示（移動、クリック、ホイール、XButton）
- 5秒ごとに統計情報を表示
- XButtonの詳細情報（mouseData、flags）も表示

**出力例:**
```
[WM_XBUTTONDOWN] pos=(100, 200) mouseData=0x00010000 flags=0x00000000 → XButton1
[WM_XBUTTONUP] pos=(100, 200) mouseData=0x00010000 flags=0x00000000 → XButton1
```

### 3. test_mouse_buttons.py
**pynputとWin32 API両方をテスト**

```bash
python test_mouse_buttons.py
```

- pynputライブラリでの認識テスト
- Win32 APIでの認識テスト
- 両方を同時に実行して比較

## 実行方法

### コマンドプロンプトから
```cmd
cd C:\Users\conqu\tts
python simple_mouse_test.py
```

### 便利なバッチファイル
```cmd
run_mouse_debug.cmd
```
をダブルクリック

## トラブルシューティング

### XButtonが認識されない場合

1. **マウスドライバーを確認**
   - ロジクール、Razerなどのマウスソフトウェアがボタンを上書きしている可能性
   - マウス設定でサイドボタンを「標準（戻る/進む）」に設定

2. **管理者権限で実行**
   ```cmd
   # 管理者としてコマンドプロンプトを開く
   cd C:\Users\conqu\tts
   python simple_mouse_test.py
   ```

3. **マウスボタンの物理的な確認**
   - ブラウザで「戻る」「進む」ボタンが動作するか確認
   - デバイスマネージャーでマウスが正しく認識されているか確認

### 出力が何も表示されない場合

1. **Pythonパスを確認**
   ```cmd
   python --version
   where python
   ```

2. **スクリプトの実行権限**
   - エクスプローラーでスクリプトを右クリック → プロパティ → ブロック解除

3. **セキュリティソフト**
   - アンチウイルスがフックをブロックしている可能性

## ritsu.pyでの動作

テストスクリプトでXButtonが認識できれば、ritsu.pyでも動作するはずです。

**ritsu.py起動後:**
- XButton1（Backボタン）→ GUI小窓のトグル
- XButton2（Forwardボタン）→ PTT（押してる間録音、離したら送信）

**ログで確認:**
```
[HOTKEY] Mouse hook installed
[HOTKEY] XButton1(Back)=toggle, XButton2(Forward)=PTT
[HOTKEY] Message pump thread started
```

このメッセージが表示されれば、フックは正常にインストールされています。
