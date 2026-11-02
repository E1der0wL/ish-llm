"""Remembered rules and individual approvals; storage stays in llm services."""

import json
from enum import StrEnum

from jsonschema import Draft202012Validator, ValidationError
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import ConditionalContainer, DynamicContainer, HSplit, Window
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ...backend.interactions.approval import ApprovalReview, risk_label, review_lines
from ...backend.interactions.approval_management import ApprovalAction
from ...backend.interactions.remembered import ToolApprovalConditions
from ...backend.interactions.command_range import CommandRangeApproval, CommandChain
from ...config.theme_style import ThemeSelector
from ...locales.keys import MessageKey
from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ...ui.input.registry import ShortcutRegistry
from ..actions import ActionBar
from ..controls import Button, ScrollbarMargin, TextArea
from ..dialog import Dialog, PopupSize
from ..feedback.progress import ProgressCompletion
from ..section import Heading
from ..sidebar import SidebarItem, SidebarList
from ..tab_page import TabPage
from .settings import SettingField, MISSING


class ApprovalTab(StrEnum):
    RULES = 'rules'
    PENDING = 'pending'


class ApprovalManager(Dialog):
    def __init__(self, view, project_id, *, pending=False, on_return=None):
        self.view, self.project_id = view, project_id
        self.on_return = on_return
        self.catalog = None
        self.busy = self.closed = False
        self.status = ''
        self.selected = {tab: None for tab in ApprovalTab}
        self.items = {tab: [] for tab in ApprovalTab}
        self.lists = {}
        self.details = {}
        pages = []
        self.action_groups = {}
        for tab, title, info_title in (
            (ApprovalTab.RULES, MessageKey.APPROVAL_RULES_TAB, MessageKey.APPROVAL_RULE_INFO),
            (ApprovalTab.PENDING, MessageKey.APPROVAL_PENDING_TAB, MessageKey.APPROVAL_REQUEST_INFO),
        ):
            listing = SidebarList(lambda tab=tab: self.items[tab],
                lambda tab=tab: self.selected[tab], lambda key, tab=tab: self.select(tab, key))
            registry = ShortcutRegistry().attach(listing)
            registry.add([KeyName.UP, KeyName.DOWN], ShortcutLabel.VERTICAL, ShortcutAction.SELECT,
                lambda e, listing=listing: listing.move(-1 if e.key_sequence[-1].key == KeyName.UP else 1))
            listing.key_bindings = registry.bindings
            self.lists[tab] = listing
            detail = TextArea(text='', read_only=True, focusable=False, wrap_lines=True,
                              height=5, style=ThemeSelector.POPUP_LIST.style)
            self.details[tab] = detail
            actions = (
                ((MessageKey.APPROVAL_EDIT_RULE, self.edit),
                 (MessageKey.APPROVAL_REVOKE, self.revoke))
                if tab == ApprovalTab.RULES else
                ((MessageKey.APPROVAL_RECLASSIFY, self.reclassify),
                 (MessageKey.APPROVAL_APPLY, self.apply_policy),
                 (MessageKey.APPROVAL_CANCEL, self.cancel_approval))
            )
            buttons = [Button(view.t(MessageKey.APPROVAL_DETAILS), handler=self.full_details),
                       *[Button(view.t(key), handler=handler) for key, handler in actions],
                       Button(view.t(MessageKey.HISTORY_CLOSE), handler=self.close)]
            self.action_groups[tab] = ActionBar(buttons, lambda: self._width().preferred - 4,
                                                equal_width=True)
            pages.append((tab, view.t(title), HSplit([
                Window(height=1), Heading(view.t(title)),
                ConditionalContainer(Label(view.t(MessageKey.APPROVAL_EMPTY), style=ThemeSelector.MUTED.style),
                                     Condition(lambda tab=tab: not self.items[tab])),
                Window(listing, height=Dimension(min=2, weight=1),
                       style=ThemeSelector.POPUP_LIST.style,
                       right_margins=[ScrollbarMargin(display_arrows=True)]),
                Window(height=1), Heading(view.t(info_title)), detail,
            ])))
        self.tabs = TabPage(pages,
            selected=ApprovalTab.PENDING if pending else ApprovalTab.RULES,
            width=lambda: self._width().preferred - 4,
            on_change=lambda key: self.reload(),
            on_enter=lambda: get_app().layout.focus(self.lists[self.tabs.selected]))
        super().__init__(title=view.t(MessageKey.APPROVAL_MANAGER),
            body=HSplit([self.tabs, Window(height=1),
                         DynamicContainer(lambda: self.action_groups[self.tabs.selected])]),
            size=PopupSize.MEDIUM,
            status_bar=view.progress.container(lambda: self.status, surface=self))
        self.on_close = self.close

    def open(self):
        self.view.dialogs.show(self, self.tabs.buttons[0 if self.tabs.selected == ApprovalTab.RULES else 1],
                               parent=self.view._dialog)
        self.reload()

    def dispose(self):
        self.closed = True

    def close(self):
        if self.busy:
            return
        self.on_close = None
        self.view.close_dialog()
        if self.on_return:
            self.on_return()

    def reload(self):
        self.call(ApprovalAction.LIST)

    def call(self, action, payload=None, *, success=None):
        if self.busy or self.closed or not self.view.on_manage_approvals:
            return
        self.busy = True
        token = self.view.progress.start(self.view.t(MessageKey.PROGRESS_BACKEND_READ
            if action == ApprovalAction.LIST else MessageKey.PROGRESS_BACKEND_WRITE))
        payload = {**(payload or {}), 'include_interactions': self.tabs.selected == ApprovalTab.PENDING}

        def done(catalog):
            self.view.progress.finish(token)
            self.busy = False
            if self.closed:
                return
            if catalog is not None:
                self.catalog = catalog
                if action == ApprovalAction.APPLY:
                    success_key = MessageKey.APPROVAL_RESUMED if catalog.resumed else MessageKey.APPROVAL_NOT_RESUMED
                    self.status = self.view.t(success_key)
                elif success:
                    self.status = self.view.t(success)
                elif not catalog.enabled:
                    self.status = self.view.t(MessageKey.APPROVAL_DISABLED)
                else:
                    self.status = ''
                self.refresh()
                # A tab may change during a read. A rules-only reply cannot
                # satisfy the pending tab; fetch that explicit view once.
                if self.tabs.selected == ApprovalTab.PENDING and not payload['include_interactions']:
                    self.reload()
            else:
                self.status = self.view.t(MessageKey.TOOLS_FAILED)
            get_app().invalidate()

        try:
            self.view.on_manage_approvals(self.project_id, action, payload,
                                         ProgressCompletion(self.view.progress, token, done))
        except Exception:
            self.view.progress.finish(token)
            self.busy = False
            raise

    def scope(self, rule):
        identifier = rule.remembered.scope.session_id
        return self.view.t(MessageKey.APPROVAL_SCOPE_SESSION, id=identifier) if identifier else self.view.t(MessageKey.APPROVAL_SCOPE_PROJECT)

    def refresh(self):
        self.items[ApprovalTab.RULES] = [SidebarItem(rule.id,
            self.rule_title(rule),
            self.scope(rule) + ' • ' + self.view.t(MessageKey.APPROVAL_REVOKED if rule.remembered.revoked_at else MessageKey.APPROVAL_ACTIVE))
            for rule in self.catalog.rules]
        self.items[ApprovalTab.PENDING] = [SidebarItem(entry.request.id, entry.request.title,
            entry.session_title + ' • ' + self.view.t(MessageKey.APPROVAL_PENDING_LABEL
                if entry.view['response'] is None else MessageKey.APPROVAL_GRANTED_LABEL))
            for entry in self.catalog.interactions]
        for tab in ApprovalTab:
            if self.selected[tab] not in {item.key for item in self.items[tab]}:
                self.selected[tab] = None
            self.select(tab, self.selected[tab])

    @staticmethod
    def rule_title(rule):
        condition = rule.remembered.condition
        ranged = CommandRangeApproval.config(condition)
        action = ranged['action'] if ranged else ToolApprovalConditions.exact_action(condition) or {}
        tool = action.get('tool', rule.category)
        arguments = action.get('arguments', {})
        target = (' / '.join(CommandChain.text([prefix]) + ' …' for prefix in ranged['prefixes'])
                  if ranged else next((str(value) for key, value in arguments.items()
                                       if ApprovalReview.target_argument(key)), ''))
        target = ' '.join(target.split())
        return tool + (' • ' + target[:66] + ('…' if len(target) > 66 else '') if target else '')

    def rule(self):
        return next((rule for rule in self.catalog.rules
                     if rule.id == self.selected[ApprovalTab.RULES]), None) if self.catalog else None

    def entry(self):
        return next((entry for entry in self.catalog.interactions
                     if entry.request.id == self.selected[ApprovalTab.PENDING]), None) if self.catalog else None

    def select(self, tab, key):
        self.selected[tab] = key
        t = self.view.t
        rule, entry = self.rule(), self.entry()
        if tab == ApprovalTab.RULES and rule:
            text = t(MessageKey.APPROVAL_RULE_DETAIL, scope=self.scope(rule),
                state=t(MessageKey.APPROVAL_REVOKED if rule.remembered.revoked_at else MessageKey.APPROVAL_ACTIVE),
                category=rule.category, risk=rule.max_risk if rule.max_risk is not None else 'unknown',
                id=rule.id, revision=rule.remembered.revision)
            ranged = CommandRangeApproval.config(rule.remembered.condition)
            text += '\n' + t(MessageKey.APPROVAL_RANGE if ranged else MessageKey.APPROVAL_EXACT)
            if ranged:
                text += ' • ' + ' / '.join(CommandChain.text([prefix]) + ' …' for prefix in ranged['prefixes'])
        elif tab == ApprovalTab.PENDING and entry:
            text = t(MessageKey.APPROVAL_PENDING_DETAIL, session=entry.session_title, run=entry.run_id,
                state=t(MessageKey.APPROVAL_PENDING_LABEL if entry.view['response'] is None else MessageKey.APPROVAL_GRANTED_LABEL),
                risk=risk_label(entry.request.risk_scheme, entry.request.risk))
        else:
            text = t(MessageKey.APPROVAL_SELECT_RULE if tab == ApprovalTab.RULES else MessageKey.APPROVAL_SELECT_REQUEST)
        self.details[tab].text = text
        get_app().invalidate()

    def full_details(self):
        if self.busy:
            return
        rule, entry = self.rule(), self.entry()
        if self.tabs.selected == ApprovalTab.RULES and rule:
            text = self.details[ApprovalTab.RULES].text + '\n\n' + json.dumps(
                rule.remembered.condition.to_dict(), ensure_ascii=False, indent=2)
        elif self.tabs.selected == ApprovalTab.PENDING and entry:
            text = '\n'.join(review_lines(entry.request.to_dict(), self.view.t)) + '\n\n' + json.dumps(
                entry.request.action, ensure_ascii=False, indent=2)
        else:
            return
        area = TextArea(text=text, read_only=True, scrollbar=True, wrap_lines=True,
                        style=ThemeSelector.POPUP_LIST.style)
        self.view.dialogs.open(self.view.t(MessageKey.APPROVAL_DETAILS), area,
                               focus=area, parent=self)

    def rule_payload(self):
        rule = self.rule()
        return {'id': rule.id, 'revision': rule.remembered.revision}

    def entry_payload(self):
        entry = self.entry()
        return {'session_id': entry.session_id, 'run_id': entry.run_id,
                'request': entry.request.to_dict()}

    def revoke(self):
        if self.busy or not self.rule() or self.rule().remembered.revoked_at:
            return
        payload = self.rule_payload()
        self.view.dialogs.open(self.view.t(MessageKey.APPROVAL_REVOKE),
            Label(self.view.t(MessageKey.APPROVAL_REVOKE_CONFIRM)),
            lambda: self.call(ApprovalAction.REVOKE, payload, success=MessageKey.APPROVAL_RULE_REVOKED),
            size=PopupSize.CUSTOM, parent=self)

    def cancel_approval(self):
        if self.busy or not self.entry():
            return
        payload = self.entry_payload()
        self.view.dialogs.open(self.view.t(MessageKey.APPROVAL_CANCEL),
            Label(self.view.t(MessageKey.APPROVAL_CANCEL_CONFIRM)),
            lambda: self.call(ApprovalAction.CANCEL, payload, success=MessageKey.APPROVAL_REQUEST_CANCELLED),
            size=PopupSize.CUSTOM, parent=self)

    def apply_policy(self):
        if not self.busy and self.entry():
            self.call(ApprovalAction.APPLY, self.entry_payload())

    def edit(self):
        rule = self.rule()
        if self.busy or rule is None:
            return
        action = ToolApprovalConditions.exact_action(rule.remembered.condition)
        ranged = CommandRangeApproval.config(rule.remembered.condition)
        if rule.remembered.revoked_at or action is None and ranged is None:
            self.status = self.view.t(MessageKey.APPROVAL_READONLY_RULE)
            return
        fields = ({'prefixes': SettingField(self.view.t(MessageKey.APPROVAL_PREFIXES),
            {'type': 'string', 'description': self.view.t(MessageKey.APPROVAL_PREFIXES_HELP)},
            CommandChain.text(ranged['prefixes']), self.view.t)} if ranged else
            {'arguments': SettingField(self.view.t(MessageKey.APPROVAL_ARGUMENTS),
            {'type': 'object', 'description': self.view.t(MessageKey.APPROVAL_ARGUMENTS_HELP)},
            action['arguments'], self.view.t)})
        if rule.max_risk is not None:
            fields['max_risk'] = SettingField(self.view.t(MessageKey.APPROVAL_MAX_RISK),
                {'type': 'integer', 'minimum': 0, 'description': self.view.t(MessageKey.APPROVAL_MAX_RISK_HELP)},
                rule.max_risk, self.view.t)
        def validate(values):
            if ranged:
                CommandRangeApproval.condition(ranged['action'], CommandChain.prefixes(values['prefixes']))
            else:
                ToolApprovalConditions.replace_arguments(action, values['arguments'])
        self.edit_fields(MessageKey.APPROVAL_EDIT_RULE, MessageKey.APPROVAL_EDIT_NOTE,
            fields, ApprovalAction.UPDATE, self.rule_payload(), MessageKey.APPROVAL_RULE_SAVED,
            validate=validate)

    def reclassify(self):
        entry = self.entry()
        if self.busy or entry is None:
            return
        if not self.catalog.risk_scheme:
            self.status = self.view.t(MessageKey.APPROVAL_NO_SCHEME)
            return
        field = SettingField(self.view.t(MessageKey.APPROVAL_RISK_SCORE),
            {'type': 'integer', 'minimum': 0, 'description': self.view.t(
                MessageKey.APPROVAL_RISK_HELP, scheme=self.catalog.risk_scheme)},
            entry.request.risk if entry.request.risk is not None else MISSING, self.view.t)
        self.edit_fields(MessageKey.APPROVAL_RECLASSIFY, MessageKey.APPROVAL_RISK_NOTE,
            {'risk': field}, ApprovalAction.RECLASSIFY,
            {**self.entry_payload(), 'risk_scheme': self.catalog.risk_scheme}, MessageKey.APPROVAL_RECLASSIFIED)

    def edit_fields(self, title, notice, fields, action, payload, success, *, validate=None):
        def save():
            try:
                values = {key: field.value() for key, field in fields.items()}
                for key, field in fields.items():
                    Draft202012Validator(field.schema).validate(values[key])
                if validate:
                    validate(values)
            except (ValueError, TypeError, ValidationError) as error:
                popup.status = str(error)
                return
            self.view.close_dialog()
            self.call(action, {**payload, **values}, success=success)

        popup = Dialog(title=self.view.t(title),
            body=HSplit([Label(self.view.t(notice), style=ThemeSelector.MUTED.style),
                         Window(height=1), *[field.container for field in fields.values()]]),
            buttons=[Button(self.view.t(MessageKey.SETTINGS_SAVE), handler=save),
                     Button(self.view.t(MessageKey.CANCEL), handler=self.view.close_dialog)])
        popup.status = ''
        popup.status_bar = self.view.progress.container(lambda: popup.status, surface=popup)
        popup.dispose = lambda: [field.finish() for field in fields.values()]
        self.view.dialogs.show(popup, next(iter(fields.values())).input, parent=self)
