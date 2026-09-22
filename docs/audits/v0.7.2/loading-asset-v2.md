# P2 加载指示 / 工具行动图：v2（自研资产，进行中）

**日期**：2026-09-22 · **阶段**：v0.7.2 P2（第二轮）· **状态**：**待用户选定**（未完成，不许当已完成）

## D1′ 的约束（为什么必须自制）

工具行「运行中」前缀图标与正文前的加载指示都用**动图**。旧实现直接引用 aiduPOP / CLS / FRY
三家源码里硬编码的同一个 `img_key`（`img_v3_02vb_496bec09-…`）—— 那是**借来的**资产：我们的 app
只能引用、**不能下载**（`im.v1.image.get` 会回 `234008 The app is not the resource sender`）。
所以 D1′（用户 2026-09-22 拍板）＝**我们自己生成 + 用自己的 app 上传一次**，源文件与生成脚本入库。

历史：`docs/audits/v0.7.2/loading-asset.md`（v0.7.1 时期）记录了「共享 key 在真机会动」这一步，
那份结论**仍然成立**，但 D1′ 之后它只作为**最后回落**保留在 `cardview.SPINNER_IMG_KEY`。

## 第一轮候选（已作废）

| 候选 | `img_key` | 结局 |
| --- | --- | --- |
| A-条状-灰 / A-条状-蓝 | `img_v3_0215p_2808e223-…` / `img_v3_0215p_6488dcf7-…` | ❌ 用户 2026-09-22：「外观不行」 |
| B-弧线-灰 / B-弧线-蓝 | `img_v3_0215p_7c38c935-…` / `img_v3_0215p_5d825b85-…` | ❌ 同上 |

用户原话：「我收到你发的调图卡了，**灰色可以**，但是我觉得你给的外观不行，不能仿照之前用的
aiduPop 的资产吗」⇒ 颜色定为**灰**✓；形状必须是 aiduPOP 那种。

## 参考物：aiduPOP 的资产长什么样（本地证据）

aiduPOP 的本地检出在 `~/.larkdeck-scratch/route-audit/aiduPOP`：

* `cardkit/elements.py:103` `_LOADING_IMG_KEY = "img_v3_02vb_496bec09-…"`、`:164 _loading_element()`
  = `div` + `icon.custom_icon(img_key, size 16px 16px)` + `plain_text " "`（与我们形状一致）；
* **官方截图** `assets/screenshots/01-instant-response.png`：卡片体里那行 loading 元素实际是
  **三个圆点**（左深、中灰、右浅）—— 就是「正在输入…」那种打字指示器。

⇒ 第一轮那两版（8 根短条 / 弧线）根本不是这个语言，用户说「不像」是对的。

## 第二轮候选（等用户选）

| 候选 | 形状 | 生成参数 | `img_key` |
| --- | --- | --- | --- |
| **C** | 三点 · 灰 | `--variant dots --color grey` | `img_v3_0215p_ad8dd428-42e2-4c40-9140-6c1f14f5091g` |
| **D** | 三点 · 深灰（头部更黑，更贴参考图对比度） | `--variant dots --color grey_dark` | `img_v3_0215p_63842d0e-4184-4d35-b043-20e9565c05cg` |

* 挑图卡：`om_x100b6411a860d8b4dd88420cef6dc33`（第 ① 行是**现役那张**当作参照）。
* 生成器：`tools/make_spinner_gif.py`（`dots` 变体，12 帧 / 70ms / 24×24 画布 ⇒ 卡片按 16px 渲染；
  纯色实心 + 二值透明 + 自建调色板 —— 见脚本 docstring 里那两条 GIF 纪律）。
* 上传：`tools/upload_card_asset.py`（一次性；本仓不保存 app 凭证）。

## 第三轮候选（用户三条反馈后的重画）

用户 2026-09-22 对 C/D 的反馈（原话）：「C 和 D 的颜色都还行，但是**看不出具体差别**。重点是
相比原来 aiduPop 的，**清晰度不够**，然后**速度比较慢**，可以适当快一点，再就是现在**每个点
一样大，不够灵动**，也可以参照一下 aiduPop 的」。

| 改动 | 之前（C/D） | 现在（E） |
| --- | --- | --- |
| 画布 | 24×24（渲染 16px ⇒ 1.5× 降采样，边缘糊） | **48×48**（3× 降采样，边缘干净） |
| 速度 | 12 帧 × 70ms = 840ms/轮 | **10 帧 × 50ms = 500ms/轮**（快 1.7×） |
| 点径 | 三点**同径** 5px | **随亮度波起伏**：头部最大 4.3px、基准 3.3px、尾部最小 2.7px |
| 颜色 | C 灰 / D 深灰（用户看不出差别） | 取**深灰**（对比度更高，更贴 aiduPOP 参考图） |

* `img_key`：`img_v3_0215p_7354240c-b1c5-46d0-86c1-b335742fd29g`（`assets/` 落地前只在挑图卡里引用）
* 挑图卡 v3：`om_x100b64123e8c38a0c125cab997e7ed7`（① aiduPOP 原图作参照，② 新版 E）
* 复现：`PY tools/make_spinner_gif.py --variant dots --color grey_dark --out assets/spinner-tool.gif`

## 落库清单（选定后立刻做，缺一不可）

1. `python3 tools/make_spinner_gif.py --variant dots --color <选中的> --out assets/spinner-tool.gif`
   （**字节可复现**：生成脚本是唯一真源）；
2. `cardview.SPINNER_TOOL_IMG_KEY` 改成上传得到的 `img_key`（**只改这一行**；
   `spinner_img_key()` 的优先级是 运行时注入 > 自研 > 三家共享）；
3. `tests/check_cardview.py`：spinner 字面量断言换成新 key，并把
   「`assets/spinner-tool.gif` 存在 ⇒ `SPINNER_TOOL_IMG_KEY` 必须**不等于**共享 key」这条守卫留在门禁里；
4. 重生成黄金夹具（`running` 行的 `img_key` 会变）⇒ 全量重验（helper 指纹变）。

⚠️ **在 2 完成之前，`SPINNER_TOOL_IMG_KEY` 只是共享 key 的别名**（`= SPINNER_IMG_KEY`）——
那是过渡态，**任何文档/提交信息都不许写成「自研资产已上线」**。
