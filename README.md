# 校园人像导拍

## 队友用手机实地测试

**手机入口：[打开校园人像导拍](https://xuehaiwuya1214.github.io/new-version/)**。把这个链接发给队友即可。手机用可安装的网页应用（PWA），第一次联网下载约 **128 MB**。识别和拍摄都在手机上完成，可以独立带到校园使用。GitHub 的「Code → Download ZIP」是供电脑开发使用的源码。

1. **添加到主屏幕。** Android 用 Chrome 打开上面的链接，点右上角菜单 →「添加到主屏幕」或「安装应用」→「安装」。iPhone 用 Safari 打开链接，点「分享」→「添加到主屏幕」→「添加」。如果有「作为网页 App 打开」，请开启。收到微信群链接时，可先用「在浏览器打开」切到 Chrome 或 Safari。也可以直接在浏览器标签页测试。[Android 操作说明](https://support.google.com/chrome/answer/9658361?hl=zh-Hans&co=GENIE.Platform%3DAndroid) · [iPhone 操作说明](https://support.apple.com/zh-cn/guide/iphone/iphea86e5236/ios)
2. **出发前下载。** 从准备外拍时使用的桌面图标或浏览器打开应用，连 Wi-Fi 点「下载离线包」，保持页面打开，直到顶部显示「离线包已就绪 · 可断网取景」。下载中断可点「继续下载」。可以打开飞行模式，关闭后从同一图标重新进入，确认仍显示「已就绪」。清理浏览器数据、切换浏览器或重装后，需要重新下载。
3. **准备参考照。** 先把选好的校园人像原图存到手机相册或「文件」，保证离线也能打开。点「打开照片」选择 JPG、PNG 或 WebP，等待识别主角和地标。首次启动引擎和分析参考照可能需要一会儿。队友已有的参考图库可直接使用，公开仓库只附原图指纹和机位文字。也可以选自己拍的人像。
4. **取景。** 点「开启相机」，允许摄像头权限，默认使用后摄。到参考地点转动手机对齐地标，再按站位框和白色动作轮廓调整人物。每一步都可点「跳过」。在室内熟悉流程时，可把同一张校园人像同时选作参考照和「用照片试拍」，观察三步是否依次通过。
5. **保存到手机。** 当快门亮起，点「拍摄并保存」。核验成功后，取景器下方会出现本次照片。点「保存到相册或分享」选择系统的保存选项，或点「下载 JPEG」到下载目录；在 iPhone 上也可长按这张照片保存。照片不带引导线。参考照和取景画面只在本机处理。

**常见情况**

- **开启相机被拒绝。** 在浏览器的网站权限和系统的浏览器相机权限中允许摄像头，回到上面的 HTTPS 链接重试。浏览器内的「用照片试拍」也能走完整引导。
- **导入相册照片失败。** 如果原图是 HEIC，而文件选择器没有提供兼容照片，请先导出为 JPEG。参考照限 24 MB、4500 万像素。
- **机位一直未对上。** 确认能看到参考中的同一段建筑边缘或固定景物。室内测试或遮挡较多时可点「跳过机位」，直接测试人物。现场反馈请记下手机型号、浏览器、参考照编号以及卡住的步骤。

## 电脑本机运行

双击 `start.cmd`，用 Edge 或 Chrome 打开[本机取景页](http://127.0.0.1:7861/)。从「打开照片」载入上游已经选好的参考照，再点「开启相机」。也可以点「用照片试拍」，直接比较另一张校园照片。多人照片可在参考照和取景画面上分别点选主角。

模型随目录提供，运行时在本机推理。启动器优先使用本目录的虚拟环境，其次使用仓库已有的 `.venv`。在新电脑上，可在本目录用 Python 3.13 建立环境。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.cmd
```

## 手机版本如何发布

推送到 `main` 后，GitHub Actions 从 `mobile/build.py` 构建静态页面并部署到 GitHub Pages。新仓库首次部署时，在 Settings → Pages → Build and deployment 中将 Source 设为 GitHub Actions。页面、模型与运行库均来自同一站点，离线下载会核对每个文件的 SHA-256。应用更新在关闭旧页面后生效。

手机后台 Worker 用 Pyodide 运行与桌面共用的取景逻辑和 OpenCV，用 ONNX Runtime Web 的单线程 WebAssembly 执行五个模型。后台只分析一帧，完成后再采下一帧，预览和按钮继续响应。机位、站位、姿势的判定阈值与桌面相同。实际识别速度取决于设备、参考图里的人物大小和背景特征。[Pyodide](https://pyodide.org/) · [ONNX Runtime Web](https://onnxruntime.ai/docs/tutorials/web/env-flags-and-session-options.html)

本地构建手机站点需要 Python 3.11 或以上。首次构建下载固定版本的运行库，之后可用本地缓存重复构建。在项目根目录运行以下命令，再在电脑浏览器打开 `http://127.0.0.1:8000/`。手机实测使用上方 HTTPS 入口。

```sh
python mobile/build.py
python -m http.server 8000 --bind 127.0.0.1 --directory build/mobile
```

## 拍摄时怎么跟

| 阶段 | 取景器里的操作 | 判定依据 |
| --- | --- | --- |
| 机位 | 参考照有清晰塔楼时，移动镜头，让真实建筑贴合固定的金色轮廓。普通场景用青色圆心和中央金圈对齐。稳住后会按地标间距自动变焦。 | 两种显示共用背景匹配。中心误差在画幅短边的 8% 以内，地标间距达到参考间距的约 89%～113%，且中心、倍率和倾角稳定 0.32 秒，即可通过。后续允许 10% 的圆心偏移和约 85%～118% 的间距。 |
| 站位 | 金色虚线框表示参考人物的位置和大小，青色框角表示当前主角。按提示让模特大致站进目标框。 | 两个人物框的交集占并集至少 45%，持续 0.35 秒即通过。调整姿势时允许降到 35%，便于抬手、迈步。 |
| 姿势 | 白线勾出参考主角的头、身体和四肢，固定在取景画面上。模特照着轮廓摆动作，青点跟踪真人，箭头指向当前最需要靠近的参考关节。 | 逐帧比较同一画幅中对应关节的位置及头肩倾角。用参考人物尺度给关节留出容差，保持 0.50 秒即通过。半身照只考核参考中可靠的部位。 |
| 拍摄 | 出现「可以拍摄」后点「拍摄并保存」。 | 快门会用按下时的画面再核验保留的步骤，然后保存同一帧的干净 JPEG。 |

机位、站位和姿势的提示旁都有「跳过」按钮。点击后立即进入下一步，本轮会一直保留这个选择，进度栏会标记「已跳过」。三步都跳过可直接手动拍摄。点「恢复全部步骤」或打开新的参考照即可重新开始自动引导。参考人物太小、暂时得不到骨架时，仍可跟随机位提示并跳过人物步骤。

钟楼、圆塔或灯塔的形状清晰时，参考照和取景器会显示建筑的金色轮廓。轮廓固定在参考构图的位置和大小，先于实时背景匹配出现，方便先找到正确机位。塔顶在画外、钟面和外墙清楚的近景可用钟面和墙边对齐。塔太小、遮挡明显或只能看到栏杆时，程序回到双圈。校园原图可利用已有机位标注，新照片也会按自身内容判断。

双圈模式会在参考照和取景器相同的位置画出最多四个带编号的白色菱形。先找到照片里的同一段屋檐、窗角或台阶。附近的背景点匹配可靠时，取景器会用浅蓝点和短虚线标出对应位置。中央金圈固定，青圈表示参考画面中心在镜头中的位置。青圈在画外时，箭头指向要转动的一侧。

机位接近并稳住后，系统比较多对地标间距，按比例在 1.0～1.6× 范围内调整数字倍率，再采一帧核对中心和景别。例如，同一段屋檐在镜头里只有参考图的七成宽，倍率会从 1× 调到约 1.43×。到达倍率边界仍有偏差时，相机会提示靠近或后退。近处和远处的地标间距变化不一致时，会提示调整拍摄位置。照片试拍、相机预览、人物引导和保存的 JPEG 共用同一套取景裁切。

动作轮廓来自用户选中的参考人物。人物分割可靠时，白线沿真实外缘描画，也保留弯臂形成的空隙和部分内部线条。分割细节不足时，根据可见关节绘制简化动作轮廓，取景器图例会标出这一点。拍摄的 JPEG 只包含原始取景画面。[鹿酱的取景演示](https://www.douyin.com/video/7641535087246702202) · [华为导入参考姿势的说明](https://consumer.huawei.com/cn/support/content/zh-cn16094218/)

室内书架和窗框也可以充当地标。近景会用 PP-HumanSeg 保留头肩两侧的真实背景。如果连续两帧的背景证据都明显支持相反的左右方向，页面会自动水平翻转参考预览，重新提取同一主角的目标姿势。参考照下方可以手动翻转或还原。拍摄结果沿用相机方向。

## 人体识别和实现

- `people.py` 用 YOLOX-Tiny HumanArt 先找人，再把主角裁成 192 × 256 的输入交给 RTMPose-m Body7，解码 17 个 COCO 关节。全图没有清晰主角时会放大局部重检。参考照常规方向无法建模时会尝试旋转，并把结果映回原图。路人进入画面时优先跟踪已选的人。[RTMPose 官方项目](https://github.com/open-mmlab/mmpose/tree/main/projects/rtmpose) · [rtmlib 的检测与姿态流程](https://github.com/Tau-J/rtmlib/blob/main/rtmlib/tools/solution/body.py)
- `outline.py` 用 PP-HumanSeg 分割参考照中选定的主角，将轮廓映射到原图坐标。每张参考照只提取一次，翻转参考照或重新点选主角时重新提取。`posture.py` 比较站位框和固定关节。参考点置信度阈值为 0.52，实时点为 0.36，参考中可靠的关节在当前帧消失时会提示露出该部位。
- `background.py` 先在人物以外比对 RootSIFT 特征，传统匹配找到的对应点不足时，再用 XFeat 和 MAGSAC 寻找同一地标。XFeat 的结果至少需有 24 个不同位置的内点，并在两幅图中都有足够分布。`landmarks.py` 用多对相隔较远的背景点测量相对倍率。`guide.py` 负责稳定判定、阶段推进和回退。[OpenCV 特征匹配与单应变换](https://docs.opencv.org/4.x/d1/de0/tutorial_py_feature_homography.html) · [XFeat 官方实现](https://github.com/verlab/accelerated_features)
- `building.py` 在载入参考照时使用量化的 SegFormer-B0 分割天空、建筑、钟面与人物。它筛选两侧有天空、比例和高度合适的塔身，在原图上用 GrabCut 细化轮廓。钟楼近景可单独提取钟面及相邻墙边。轮廓使用参考画幅的归一化坐标，翻转参考照时会从翻转后的画面重新生成。[SegFormer 模型说明与许可](https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512)

先对圆心、稳住手机再变焦的交互参考了华为的辅助构图说明。本项目用上传的参考照提供构图目标。单帧相对深度能描述景物的远近，复拍还需要确认两张图里具体哪几处是同一地标，因此当前使用跨图匹配测量倍率。[华为操作说明](https://consumer.huawei.com/cn/support/content/zh-cn16073539/) · [Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2)

人体检测与姿态权重取自 OpenMMLab，人像分割权重取自 OpenCV Zoo，XFeat 使用 V1.0.0 的稀疏 ONNX 导出，SegFormer 使用 ADE20K 的 B0 量化导出。模型都在本机使用 ONNX Runtime CPU 推理，文件可用 PowerShell 的 `Get-FileHash models/*.onnx` 核对。SegFormer 权重适用 NVIDIA 的非商业研究或评估许可，原文随模型提供。

| 本地模型 | SHA-256 | 官方来源 |
| --- | --- | --- |
| yolox-tiny.onnx | `ceb11c07298f95c50d7c5abeb906d03340c85f23aa79e3e66966e7fb6c307250` | [YOLOX-Tiny 模型包](https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/yolox_tiny_8xb8-300e_humanart-6f3252f9.zip) |
| rtmpose-m.onnx | `5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c` | [RTMPose-m 模型包](https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip) |
| pphumanseg.onnx | `552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24` | [OpenCV Zoo PP-HumanSeg](https://github.com/opencv/opencv_zoo/tree/main/models/human_segmentation_pphumanseg) |
| xfeat.onnx | `41336466bbbb09b701b815e33932c7da69cff6ab75a068230418468430482912` | [XFeat-ONNX V1.0.0](https://github.com/DavideCatto/XFeat-ONNX/releases/tag/V1.0.0) |
| segformer-b0-ade-q8.onnx | `9a98d6daf3d926869ab8cc4c2ed7374a2bc23b889bb7ca3b0915d15e3c4756bb` | [Xenova 量化导出](https://huggingface.co/Xenova/segformer-b0-finetuned-ade-512-512/blob/d3e5499fa8701ff0453ca940a8dfeae39b2f1504/onnx/model_quantized.onnx) |

## 对接上游

1. `POST /api/session` 以 multipart 传入 `reference`。可附 `viewpoint` 机位说明、`x` 和 `y` 归一化主角点选坐标。`mirror=1` 使用水平翻转后的参考画面与点选坐标。返回 `id` 和 `reference`，其中 `centerTolerance` 为金圈半径占短边的比例，`box`、`points` 和 `outline` 均使用固定参考画幅的归一化坐标。`outline.kind` 为 `person` 或 `joints`，对应实际轮廓和简化轮廓。仓库已有的校园参考照按原文件 SHA-256 读取机位备注。独立部署时可通过 `CAMPUS_REFERENCE_INDEX` 指向队友的 `sources.json`。
2. `POST /api/session/<id>/frame?seq=1` 发送与参考照同画幅的 JPEG，帧号递增。可附 `x` 和 `y` 切换实时主角。`skip=background,position` 指定本轮跳过机位和站位，另可选 `pose`。省略 `skip` 沿用当前选择，传 `skip=` 恢复全部步骤。响应包含 `stage`、`message`、`background`、`position`、`pose` 和 `skipped`。`checks` 依次记录机位、站位和姿势的判定，已跳过的项为 `null`，`ready` 只取决于保留的步骤。
3. `DELETE /api/session/<id>` 释放会话。手机浏览器需要在 HTTPS 或 localhost 安全上下文中使用摄像头。安卓连接电脑调试时，可用 `adb reverse tcp:7861 tcp:7861` 后在手机打开本机地址。

参考响应的 `buildingOutline` 为 `{name, paths}` 或 `null`，每条折线均为固定参考坐标。`null` 表示使用双圈和 `landmarks` 中的固定地标。帧响应中 `background.anchors` 按编号给出有匹配支持的实时位置，`centerOk`、`scaleOk` 分别记录圆心和景别。稳定后出现的 `zoomFactor` 是建议乘到当前倍率的系数，客户端应用后须上送新画面继续核验。切换实时来源时可更新帧请求的整数 `source`，让服务重新累计稳定时间。

## 验证

在队友的 98 张校园照片中，93 张提取了参考人物的实际轮廓，5 张根据可见关节生成了简化轮廓。以同一张照片分别作参考和当前帧，97 张可同时通过机位、站位和姿势判定。另 1 张草地躺姿可以显示轮廓，可见关节不足以自动判断动作。这个检查衡量同图重复识别能力。独立照片对、已知位移、局部手腕变化、镜像和实际浏览器流程另行测试。

手机网页另有独立的浏览器回放。它从空缓存下载完整离线包，断网、关闭并重开页面，再用本地钟楼人像走完取景、三步判定、逐步跳过、恢复引导和 JPEG 下载。可在安装 Pillow、Playwright 和 Chromium 的开发环境中运行 `python tests/mobile_flow.py 参考人像.jpg`。若参考中预期有钟楼轮廓，可加 `--tower`；用 `--url https://xuehaiwuya1214.github.io/new-version/` 可检查实际发布的站点。手机真机的摄像头权限、系统相册以及现场帧率仍需实地测试。

同一批参考照中，19 张显示建筑轮廓，79 张使用双圈。另用一张没有校园标注的灯塔实拍照测试，识别到了可叠加的塔身外沿。[Ocracoke 灯塔测试照（美国国家公园管理局）](https://npgallery.nps.gov/AssetDetail/0e62cae5-7578-4b45-82a9-880a6e8b3b96)

把已标注机位的非拼图照片居中取 3:4 画幅后，同一机位分组内共得到 103 对不同照片。RootSIFT 找到可估计几何关系的背景匹配 13 对，加入 XFeat 后共 36 对，其中 3 对在这次取景中直接满足中心和景别阈值。另用每组首张照片组成 414 对不同地点标签的对照，有 1 对给出调整角度提示，0 对通过机位判定。

- 情人坡红椅的 `REF_051.webp` 与 `REF_052.webp` 可直接用「照片试拍」复现。地标能对上，两人像的站位仍有差距。
- 录屏可用 `REF_016.webp` 看完整钟楼，`REF_029.webp` 看钟面和两条外墙线。换成同一地点标签的栏杆照 `REF_062.webp`，即可看到双圈回退。
- 以草坡朝圆塔的 `REF_046.webp` 作参考、`REF_095.webp` 试拍，可看到真实圆塔在金色目标轮廓的左下方，系统先提示向左转。两张照片的构图和人物站位都不同。
- 以国科大桥的 `REF_103.jpg` 作参考，载入 `REF_104.jpg` 试拍并跳过机位，可看到站位框通过后，白色参考轮廓固定在画面里，青色箭头提示左肘的移动方向。
- 在本目录用现有环境运行以下命令。浏览器回放使用本机 Chromium 和可控视频流，能验证变焦、拍摄资格撤回、干净 JPEG 导出和手机宽度布局。校园实地光照变化、遮挡和移动速度仍需现场验证。

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests/test_guidance.py
..\.venv\Scripts\python.exe tests/browser_flow.py
..\.venv\Scripts\python.exe tests/check_library.py "..\校园摄影参考库_队友分享_98张\统一参考库"
```

