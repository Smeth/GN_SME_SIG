"""Global activity view, retaining specialized journal views."""
from sme_gend.ui.views.sensitive_audit_log_view import SensitiveAuditLogView


class ActivityAuditLogView(SensitiveAuditLogView):
    def __init__(self, parent=None):
        super().__init__(parent, global_activity=True)
