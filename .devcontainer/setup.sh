#!/usr/bin/env bash
set -euo pipefail

# Install tesseract and poppler for HSBC OCR parser
if ! command -v tesseract &>/dev/null || ! command -v pdfinfo &>/dev/null; then
  apt-get update -qq && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils
fi

pip install --upgrade pip
echo "Updated pip to version $(pip --version | awk '{print $2}')"

pip install -r /workspaces/fastAPI/requirements.txt
echo "Installed project dependencies"

# Claude Setup in Container
echo "Setting up ~/.claude in container..."
echo "$HOME"

if [ ! -f "$HOME/.claude/settings.json" ]; then

  echo "Seeding ~/.claude from host mount (first run)..."
  mkdir -p "$HOME/.claude"
  sudo chown -R vscode:vscode "$HOME/.claude"
  cp -R /tmp/host-claude/. "$HOME/.claude/"

  SETTINGS="$HOME/.claude/settings.json"
  original_url=$(grep -o '"ANTHROPIC_BASE_URL": *"[^"]*"' "$SETTINGS" | grep -o 'http[^"]*')
  if [ -n "$original_url" ]; then
    new_url=$(echo "$original_url" | sed -e 's|://localhost|://host.docker.internal|g' -e 's|://127\.0\.0\.1|://host.docker.internal|g')
    sed -i "s|$original_url|$new_url|g" "$SETTINGS"
    echo "Updated ANTHROPIC_BASE_URL from $original_url to $new_url"
  fi

else
  echo "~/.claude already seeded, skipping copy."
fi