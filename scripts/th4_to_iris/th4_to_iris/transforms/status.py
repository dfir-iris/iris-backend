"""Case status / resolution status mapping."""

from __future__ import annotations


TH4_CASE_STATE = {
    "Open": "Open",
    "Resolved": "Closed",
    "Deleted": "Deleted",
    "Duplicated": "Duplicated",
}

TH4_CASE_RESOLUTION = {
    "Indeterminate": "not_reviewed",
    "FalsePositive": "reviewed",
    "TruePositive": "reviewed",
    "Other": "reviewed",
    "Duplicated": "reviewed",
}

TH4_TASK_STATUS = {
    "Waiting": "To do",
    "InProgress": "In progress",
    "Completed": "Done",
    "Cancel": "Cancelled",
}

TH4_ALERT_STATUS = {
    "New": "New",
    "Updated": "In progress",
    "Ignored": "Closed",
    "Imported": "Merged",
}
