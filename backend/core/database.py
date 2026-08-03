"""数据库网关抽象：把可查询性（ping）与关闭动作与具体实现解耦。"""

from typing import Protocol


class DatabaseUnavailableError(Exception):
    """依赖不可用；错误只描述依赖种类，不回显底层驱动消息。"""


class DatabaseGateway(Protocol):
    """应用依赖的数据库最小接口。"""

    async def ping(self) -> None:
        """探测数据库可查询；失败抛 DatabaseUnavailableError。"""
        ...

    async def close(self) -> None:
        """释放连接池等资源。"""
        ...
