#!/bin/bash
# ============================================
# MR.Jobs Setup Script
# ============================================

set -e

echo "🚀 MR.Jobs Setup"
echo "================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install Python 3.10+ first."
    exit 1
fi
echo "✅ Python: $(python3 --version)"

# Check Node.js (needed for Claude Code CLI)
if ! command -v node &> /dev/null; then
    echo "❌ Node.js not found. Install Node.js 18+ first."
    echo "   brew install node  (macOS)"
    echo "   or visit https://nodejs.org"
    exit 1
fi
echo "✅ Node.js: $(node --version)"

# Check/Install Claude Code CLI
if ! command -v claude &> /dev/null; then
    echo ""
    echo "📦 Installing Claude Code CLI..."
    npm install -g @anthropic-ai/claude-code
    echo "⚠️  Run 'claude auth' to authenticate before using MR.Jobs."
else
    echo "✅ Claude CLI: $(claude --version 2>/dev/null || echo 'installed')"
fi

# Install Python dependencies
echo ""
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt --break-system-packages 2>/dev/null || pip install -r requirements.txt

# Install Playwright browsers
echo ""
echo "🌐 Installing Playwright browsers (this takes a minute)..."
python3 -m playwright install chromium

# Create .cache directory
mkdir -p .cache

# Check for profile
if [ ! -f "profile.yaml" ]; then
    echo ""
    echo "⚠️  profile.yaml exists but needs to be customized!"
    echo "   Edit profile.yaml with your personal info before running."
fi

# Check for resume
if [ ! -f "resume.pdf" ]; then
    echo ""
    echo "⚠️  No resume.pdf found in project directory."
    echo "   Place your resume as resume.pdf here, or update resume_path in profile.yaml."
fi

echo ""
echo "============================================"
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit profile.yaml with your info"
echo "  2. Place your resume.pdf in this directory"
echo "  3. Run: claude auth  (if not already authenticated)"
echo "  4. Run: python3 main.py discover"
echo "============================================"
