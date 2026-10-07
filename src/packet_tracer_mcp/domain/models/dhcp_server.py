"""Pool DHCP sobre un Server-PT (Services > DHCP en la GUI).

No es el mismo DHCP que el de un router: ahí el pool se crea por CLI IOS
(`ip dhcp pool`), mientras que un Server-PT no tiene CLI. Lo expone la API nativa
de PT, un nivel más abajo de lo que parece:

    getProcess("DhcpServerMain")                 -> DhcpServerMainProcess
      .getDhcpServerProcessByPortName("FastEthernet0") -> DhcpServerProcess
        .addPool(name) / .getPool(name) / .setEnable(bool)
          -> DhcpPool: setNetworkAddress, setNetworkMask(red, máscara), ...

`DhcpServerMain` en sí no tiene ningún método de pools; por eso parecía que PT
no permitía configurarlo (issue #23).
"""

from __future__ import annotations

from pydantic import BaseModel

# El pool que trae todo Server-PT de fábrica; es el que muestra la GUI.
DEFAULT_SERVER_POOL = "serverPool"


class DhcpServerPool(BaseModel):
    """Un pool DHCP servido por un Server-PT desde uno de sus puertos."""

    device: str
    pool_name: str = DEFAULT_SERVER_POOL
    port: str = "FastEthernet0"
    network: str = ""
    mask: str = "255.255.255.0"
    gateway: str = ""
    dns: str = ""
    start_ip: str = ""   # vacío = primer host libre después del gateway
    max_users: int = 0   # 0 = hasta el final de la subred
