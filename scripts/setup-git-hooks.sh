#!/bin/bash
# Install pre-commit hook that delegates to scripts/pre-commit.sh
HOOK_FILE="$(dirname "$0")/../.git/hooks/pre-commit"
cat > "$HOOK_FILE" <<'EOF'
#!/bin/bash
exec bash "$(dirname "$0")/../../scripts/pre-commit.sh"
EOF
chmod +x "$HOOK_FILE"
echo "✅ Pre-commit hook installed — runs on every 'git commit'"
