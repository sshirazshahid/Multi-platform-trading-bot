"""Central configuration package — permanent facade for `import config`."""
from config._env import *  # noqa: F403
from config.credentials import *  # noqa: F403
from config.notifications import *  # noqa: F403
from config.modes import *  # noqa: F403
from config.costs import *  # noqa: F403
from config.portfolio import *  # noqa: F403
from config.execution import *  # noqa: F403
from config.probes import *  # noqa: F403
from config.gates import *  # noqa: F403
from config.scanner import *  # noqa: F403
from config.universe import *  # noqa: F403
from config.risk import *  # noqa: F403
from config.filters import *  # noqa: F403
from config.signal import *  # noqa: F403
from config.strategies import *  # noqa: F403
from config.spot import *  # noqa: F403
from config.misc import *  # noqa: F403
from config.feeds import *  # noqa: F403
from config.gates import (  # noqa: F401
    _accuracy_geometry_enabled,
    _max_flow_research_knob_enabled,
    _profile_gated_accband_research_max_opens,
    _profile_gated_economic_gate_mode,
    _profile_gated_entry_floor,
    _profile_gated_sl_cooldown,
    _smart_money_entry_gate_enabled,
)
from config.universe import get_commodity_meta, is_analysis_only, is_commodity
