"""Email chase state machine on a virtual clock.

SENT -> FOLLOWUP_1 (day 3) -> FOLLOWUP_2 (day 6) -> ESCALATED (day 14)

Carriers reply after a configured number of follow-ups. One carrier first sends
an attachment for the WRONG policy, which must be rejected without ending the chase.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config
import seed
from agent import mailbox

SENT = "sent"
FOLLOWUP_1 = "followup_1"
FOLLOWUP_2 = "followup_2"
RECEIVED = "received"
ESCALATED = "escalated"


@dataclass
class Chase:
    policy_number: str
    carrier: str
    account: str
    state: str = SENT
    followups: int = 0
    day: int = 0
    attachments: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    # Ironwood's first reply carries the wrong policy's document.
    wrong_reply_pending: bool = False

    def __post_init__(self):
        if self.carrier == "Ironwood" and self.policy_number.endswith("0255"):
            self.wrong_reply_pending = True

    @property
    def status_label(self) -> str:
        return {
            SENT: f"Request sent · {self.day}d ago",
            FOLLOWUP_1: "Follow-up sent",
            FOLLOWUP_2: "Follow-up sent",
            RECEIVED: "Downloaded",
            ESCALATED: "Escalated - no response",
        }[self.state]

    def _followup_body(self, n: int) -> str:
        return (f"Following up on the loss run request below for {self.account}. "
                f"Renewal is {seed_expires(self.account)}, so we would appreciate "
                f"anything you can send this week.")

    def tick(self, day: int) -> None:
        """Advance the virtual clock to `day` and act if a threshold was crossed."""
        self.day = day
        if self.state in (RECEIVED, ESCALATED):
            return

        replies_after = config.CARRIERS[self.carrier].get("replies_after_followups", 1)

        if day >= config.FOLLOWUP_1_DAY and self.followups < 1:
            self.followups = 1
            self.state = FOLLOWUP_1
            mailbox.send(config.CARRIERS[self.carrier]["email"],
                         f"RE: Loss run request · {self.account} · {self.policy_number}",
                         self._followup_body(1), self.policy_number,
                         kind="followup1", day=day)
            self.log.append(f"day {day}: follow-up 1 sent")

        if day >= config.FOLLOWUP_2_DAY and self.followups < 2:
            self.followups = 2
            self.state = FOLLOWUP_2
            mailbox.send(config.CARRIERS[self.carrier]["email"],
                         f"RE: Loss run request · {self.account} · {self.policy_number}",
                         self._followup_body(2), self.policy_number,
                         kind="followup2", day=day)
            self.log.append(f"day {day}: follow-up 2 sent (auto)")

        if self.followups >= replies_after and self.state != RECEIVED:
            # A wrong-policy attachment arrives first and must be rejected.
            if self.wrong_reply_pending:
                self.wrong_reply_pending = False
                mailbox.deliver_reply(self.policy_number, self.carrier,
                                      [seed.artifact_name(self.carrier)], day,
                                      wrong_policy=True)
                self.rejected.append("attachment was for policy XX-000-0000")
                self.log.append(f"day {day}: reply rejected - attachment for wrong policy")
                return

            atts = [seed.artifact_name(self.carrier)]
            if self.carrier == "Summit":
                atts.append(seed.artifact_name(self.carrier, "prior"))
            mailbox.deliver_reply(self.policy_number, self.carrier, atts, day)
            self.attachments = atts
            self.state = RECEIVED
            self.log.append(f"day {day}: reply received with {len(atts)} attachment(s)")
            return

        if day >= config.ESCALATE_DAY and self.state != RECEIVED:
            self.state = ESCALATED
            self.log.append(f"day {day}: escalated after {self.followups} follow-ups")


def seed_expires(account_name: str) -> str:
    for a in seed.ACCOUNTS:
        if a["name"] == account_name:
            return a["expires"].strftime("%m/%d")
    return "03/01"
