export function safeId(value) {
  if (!value) return "-";
  const text = String(value);
  return text.length > 13 ? `${text.slice(0, 8)}…${text.slice(-4)}` : text;
}

export function roleList(member) {
  if (Array.isArray(member.roles)) return member.roles;
  if (Array.isArray(member.role_ids)) {
    return member.role_ids.map((id) => ({ id, name: safeId(id) }));
  }
  return [];
}

export function permissionOverrideMap(member) {
  if (Array.isArray(member?.permission_overrides)) {
    return Object.fromEntries(
      member.permission_overrides.map((item) => [item.permission_code, item.effect]),
    );
  }
  if (member?.permission_overrides && typeof member.permission_overrides === "object") {
    return { ...member.permission_overrides };
  }
  return {};
}

export function memberAccessStateKey(member) {
  const roleIds = roleList(member).map((role) => role.id).sort().join(",");
  const overrides = Object.entries(permissionOverrideMap(member))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([code, effect]) => `${code}:${effect}`)
    .join(",");
  return `${member?.membership_id || "member"}|${roleIds}|${overrides}`;
}

export function withMemberPermissionState(member, roles) {
  const assignedIds = new Set(roleList(member).map((role) => role.id));
  const inherited = new Set();
  roles.filter((role) => assignedIds.has(role.id)).forEach((role) => {
    (role.permission_codes || []).forEach((code) => inherited.add(code));
  });
  const overrides = permissionOverrideMap(member);
  const effective = new Set(inherited);
  Object.entries(overrides).forEach(([code, effect]) => {
    if (effect === "deny") effective.delete(code);
    else if (effect === "allow") effective.add(code);
  });
  return {
    ...member,
    roles: roles.filter((role) => assignedIds.has(role.id)),
    inherited_permission_codes: [...inherited].sort(),
    effective_permission_codes: [...effective].sort(),
    permission_overrides: Object.entries(overrides)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([permission_code, effect]) => ({ permission_code, effect })),
  };
}
