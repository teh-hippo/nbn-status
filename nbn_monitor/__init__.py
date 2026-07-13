"""NBN outage monitor with ntfy notifications and a traffic-light status page.

This package exposes the public API expected by ``function_app.py`` and
the CLI entry point. Internal helpers live in their respective submodules
(``config``, ``api``, ``snapshot``, ``persistence``, ``derive``, ``notify``,
``render``, ``server``, ``orchestrator``, ``cli``); they are not re-exported here.
"""

from __future__ import annotations

from .api import (
    OUTAGE_LABELS,
    OutageStatus,
    check_all,
    check_outage,
    display_outage_colour,
    display_outage_is_outage,
    display_outage_is_service_issue,
)
from .cli import main
from .config import Address, NtfyConfig, load_addresses
from .derive import derive_snapshot
from .notify import (
    PlannedDelivery,
    ServiceDelivery,
    apply_planned_deliveries,
    apply_service_deliveries,
    notify_changes,
    notify_planned_maintenance,
    seed_notification_baselines,
    send_ntfy,
)
from .orchestrator import poll, run_poll_cycle
from .persistence import load_state_result, save_state
from .planned import (
    PlannedChange,
    PlannedParseResult,
    PlannedReminders,
    PlannedScheduleDiff,
    describe_event,
    diff_complete_planned_schedule,
    diff_planned_schedule,
    due_planned_reminders,
    event_local_end,
    event_local_start,
    event_start,
    format_estimated_duration,
    format_event_date,
    format_event_end,
    format_event_time,
    material_revision,
    parse_planned_maintenance,
    upcoming_events,
    visible_events,
)
from .render import generate_html, generate_snapshot_html
from .server import make_handler, serve
from .snapshot import (
    AddressEntry,
    Period,
    PlannedMaintenance,
    PlannedNotificationState,
    ServiceNotificationState,
    ServiceResolution,
    Snapshot,
    StatusRecord,
)

__version__ = "0.1.0"

__all__ = [
    "OUTAGE_LABELS",
    "Address",
    "AddressEntry",
    "NtfyConfig",
    "OutageStatus",
    "Period",
    "PlannedChange",
    "PlannedDelivery",
    "PlannedMaintenance",
    "PlannedNotificationState",
    "PlannedParseResult",
    "PlannedReminders",
    "PlannedScheduleDiff",
    "ServiceDelivery",
    "ServiceNotificationState",
    "ServiceResolution",
    "Snapshot",
    "StatusRecord",
    "check_all",
    "check_outage",
    "apply_planned_deliveries",
    "apply_service_deliveries",
    "derive_snapshot",
    "display_outage_colour",
    "display_outage_is_outage",
    "display_outage_is_service_issue",
    "describe_event",
    "diff_complete_planned_schedule",
    "diff_planned_schedule",
    "due_planned_reminders",
    "event_local_end",
    "event_local_start",
    "event_start",
    "format_estimated_duration",
    "format_event_date",
    "format_event_end",
    "format_event_time",
    "generate_html",
    "generate_snapshot_html",
    "load_addresses",
    "load_state_result",
    "main",
    "make_handler",
    "material_revision",
    "notify_changes",
    "notify_planned_maintenance",
    "parse_planned_maintenance",
    "poll",
    "run_poll_cycle",
    "save_state",
    "seed_notification_baselines",
    "send_ntfy",
    "serve",
    "upcoming_events",
    "visible_events",
]
