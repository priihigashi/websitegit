"""
notifier.py — Push notifications via ntfy.sh.
NTFY_TOPIC secret controls which topic to publish to.
Install the ntfy app on your phone and subscribe to your topic.
"""
import os, requests

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "oak-park-content-4am")
NTFY_BASE  = "https://ntfy.sh"


def send(title, message, priority="default", tags="robot"):
    resp = requests.post(
        f"{NTFY_BASE}/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": tags},
        timeout=10,
    )
    return resp.status_code == 200


def notify_run_complete(topics, rows_added, clips_found, error=None):
    if error:
        return send(
            title="❌ 4AM Agent Failed",
            message=f"Error: {error}\nCheck Runs Log tab for details.",
            priority="high",
            tags="warning",
        )

    topic_list = "\n".join(f"• {t}" for t in topics)
    message    = (
        f"{rows_added} scripts added to Content Queue\n"
        f"{clips_found} B-roll clips found\n\n"
        f"Topics:\n{topic_list}"
    )
    return send(title="✅ 4AM Content Ready", message=message, tags="tada,robot")


def notify_new_skill(skill_name, pattern_summary):
    message = (
        f"Pattern detected in run logs.\n"
        f"Auto-created: skills/{skill_name}\n\n"
        f"Pattern: {pattern_summary}"
    )
    return send(title="🧠 New Skill Auto-Created", message=message, tags="brain,robot")


def notify_skill_task(task_title, description):
    message = (
        f"A pattern was found in run logs that needs a new skill.\n\n"
        f"{description}\n\n"
        f"Calendar task created: '{task_title}'"
    )
    return send(title="📅 Skill Task Added to Calendar", message=message, tags="calendar,robot")
