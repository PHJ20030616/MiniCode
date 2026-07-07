"""参数级权限检查系统。

基于工具名和参数进行细粒度权限判断，
返回 safe / caution / dangerous / deny 四类结果。
"""

from minicode.permissions.checker import check_permission
from minicode.permissions.models import PermissionDecision, PermissionLevel

__all__ = [
    "PermissionDecision",
    "PermissionLevel",
    "check_permission",
]
