# -*- coding: utf-8 -*-
"""
Shared helpers for persisting page workspace state.
"""

import copy


class WorkspaceStateMixin:
    PAGE_STATE_ID = ''
    WORKSPACE_SAVE_DEBOUNCE_MS = 800

    def _init_workspace_state_support(self):
        self._workspace_save_job = None
        self._workspace_state_autosave_enabled = False
        self._workspace_state_restoring = False
        self._workspace_state_restored = False

    def _enable_workspace_state_autosave(self):
        self._workspace_state_autosave_enabled = True

    def _cancel_workspace_state_save(self):
        job = getattr(self, '_workspace_save_job', None)
        if not job:
            return
        try:
            self.frame.after_cancel(job)
        except Exception:
            pass
        self._workspace_save_job = None

    def _schedule_workspace_state_save(self, _event=None):
        if not getattr(self, '_workspace_state_autosave_enabled', False):
            return
        if getattr(self, '_workspace_state_restoring', False):
            return
        if not getattr(self, 'config', None) or not getattr(self, 'PAGE_STATE_ID', ''):
            return

        self._cancel_workspace_state_save()
        try:
            self._workspace_save_job = self.frame.after(
                self.WORKSPACE_SAVE_DEBOUNCE_MS,
                self.save_workspace_state_now,
            )
        except Exception:
            self._workspace_save_job = None

    def save_workspace_state_now(self, save_to_disk=True):
        self._cancel_workspace_state_save()
        if not getattr(self, 'config', None) or not getattr(self, 'PAGE_STATE_ID', ''):
            return False
        if not hasattr(self, 'export_workspace_state'):
            return False

        try:
            state = self.export_workspace_state()
        except Exception:
            return False

        if not isinstance(state, dict):
            state = {}

        self.config.set_workspace_state(self.PAGE_STATE_ID, state)
        if save_to_disk:
            return bool(self.config.save())
        return True

    def capture_workspace_state_snapshot(self, save_to_disk=False):
        if not hasattr(self, 'export_workspace_state'):
            return {}
        try:
            state = self.export_workspace_state()
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}

        if getattr(self, 'config', None) and getattr(self, 'PAGE_STATE_ID', ''):
            self.config.set_workspace_state(self.PAGE_STATE_ID, state)
            if save_to_disk:
                self.config.save()
        return copy.deepcopy(state)

    def apply_workspace_state_snapshot(self, state, save_to_disk=True):
        if not getattr(self, 'config', None) or not getattr(self, 'PAGE_STATE_ID', ''):
            return False
        if not hasattr(self, 'restore_workspace_state'):
            return False
        if not isinstance(state, dict):
            return False

        self._cancel_workspace_state_save()
        self._workspace_state_restoring = True
        try:
            self.restore_workspace_state(copy.deepcopy(state))
        finally:
            self._workspace_state_restoring = False

        self._workspace_state_restored = True
        self.config.set_workspace_state(self.PAGE_STATE_ID, state)
        if save_to_disk:
            return bool(self.config.save())
        return True

    def restore_saved_workspace_state(self):
        if not getattr(self, 'config', None) or not getattr(self, 'PAGE_STATE_ID', ''):
            return False
        if not hasattr(self, 'restore_workspace_state'):
            return False

        state = self.config.get_workspace_state(self.PAGE_STATE_ID, default={})
        if not isinstance(state, dict) or not state:
            return False

        self._workspace_state_restoring = True
        try:
            self.restore_workspace_state(copy.deepcopy(state))
        finally:
            self._workspace_state_restoring = False

        self._workspace_state_restored = True
        return True
