class UserError(ValueError):
    """可向用户显示的固定原因；不得包含密钥、聊天正文或完整用户路径。"""
