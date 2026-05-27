import rules


@rules.predicate
def is_trigger_owner(user, trigger):
    return trigger.user == user

# Module level permission of TraceT required for admin
rules.add_perm("tracet", rules.is_group_member("admin"))

# Trigger permissions
rules.add_perm(
    "tracet.view_trigger",
    rules.is_group_member("astronomers") | rules.is_group_member("admin"),
)
rules.add_perm(
    "tracet.add_trigger",
    rules.is_group_member("admin") | rules.is_group_member("astronomers"),
)
rules.add_perm("tracet.admin_triggers", rules.is_group_member("admin"))
rules.add_perm(
    "tracet.change_trigger", rules.is_group_member("admin") | is_trigger_owner
)
rules.add_perm(
    "tracet.delete_trigger", rules.is_group_member("admin") | is_trigger_owner
)
rules.add_perm(
    "tracet.retrigger_trigger", rules.is_group_member("admin") | is_trigger_owner
)


# Allow adding/deleting Streams
rules.add_perm("tracet.view_stream", rules.is_group_member("admin"))
rules.add_perm("tracet.add_stream", rules.is_group_member("admin"))
rules.add_perm("tracet.change_stream", rules.is_group_member("admin"))
rules.add_perm("tracet.delete_stream", rules.is_group_member("admin"))

# Allow adding/deleting Topics
rules.add_perm("tracet.view_topic", rules.is_group_member("admin"))
rules.add_perm("tracet.add_topic", rules.is_group_member("admin"))
rules.add_perm("tracet.delete_topic", rules.is_group_member("admin"))

# User administration permissions
rules.add_perm("tracet.view_user", rules.is_group_member("admin"))
rules.add_perm("tracet.add_user", rules.is_group_member("admin"))
rules.add_perm("tracet.change_user", rules.is_group_member("admin"))
rules.add_perm("tracet.delete_user", rules.is_group_member("admin"))
