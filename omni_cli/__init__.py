"""OMNI CLI（`omni`）

本包是独立项目（以 git submodule 形式挂在 OMNI Link 仓库的 `vendor/omni-cli/`），
两类命令：

- 调试/烧录类：`backend` / `probe` / `target` / `flash` / `commander` / `debug` /
  `monitor` / `device` / `pack` / `rtt` / `system` / `file` / `tool`。
  通过 HTTP 复用 OMNI Link 后端的 REST 接口，命令状态保存在后端进程里。
- 工程配置/构建类：`omni config` / `omni build` / `omni toolchain` / `omni boards` /
  `omni modules`，本地执行，只读 omni-emb 框架的 Kconfig / boards 数据。
"""

__version__ = "0.42.0"
