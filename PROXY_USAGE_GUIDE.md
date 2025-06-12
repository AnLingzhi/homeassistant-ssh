# Home Assistant SSH 插件代理功能使用说明 

本文档将指导您如何安装和使用集成了代理功能的 Home Assistant SSH 插件。

## 安装步骤

1.  **定位 `custom_components` 目录**:
    在您的 Home Assistant 配置目录中，找到或创建一个名为 `custom_components` 的文件夹。

3.  **复制插件**:
    将 `homeassistant-ssh/custom_components/ssh` 整个文件夹复制到您的 `custom_components` 目录中。

    完成后的目录结构应如下所示：
    ```
    <home_assistant_config_dir>/
    └── custom_components/
        └── ssh/
            ├── __init__.py
            ├── manifest.json
            ├── ssh_terminal_manager
            └── ... 
    ```

4.  **重启 Home Assistant**:
    为了让 Home Assistant 识别并加载新的自定义插件，您需要重启 Home Assistant 核心服务。

安装完成！现在您可以开始配置和使用代理功能了。

## 功能与配置

此功能允许 SSH 插件通过指定的代理服务器（支持 SOCKS5, SOCKS4, 和 HTTP 代理）连接到您的目标 SSH 主机。

### 配置方法

您可以在添加新的 SSH 集成或重新配置现有集成时设置代理：

1.  导航到 **设置** > **设备与服务** > **添加集成**，然后选择 **SSH**。
2.  在配置表单中，除了标准的 SSH 信息外，您会看到以下代理字段：
    *   **Proxy Type**: 从下拉菜单选择代理类型 (`SOCKS5`, `SOCKS4`, `HTTP`)。
    *   **Proxy Host**: 代理服务器的地址。
    *   **Proxy Port**: 代理服务器的端口。
    *   **Proxy Username** (可选): 代理认证用户名。
    *   **Proxy Password** (可选): 代理认证密码。
3.  填写完毕后提交即可。

## 故障排查

如果遇到连接问题，请首先检查 Home Assistant 的系统日志，查找与 `custom_components.ssh` 相关的错误信息，以帮助定位问题。