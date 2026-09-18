#!/bin/bash

# Zero Log Parser Build Script
# Builds wheel packages for distribution

set -e  # Exit on any error

echo "🔧 Zero Log Parser Build Script"
echo "================================"

# Check if we're in the right directory
if [[ ! -f "pyproject.toml" ]]; then
    echo "❌ Error: pyproject.toml not found. Run this script from the project root."
    exit 1
fi

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf build/ dist/ src/zero_log_parser.egg-info/ src/zero_log_parser/__pycache__/

# Check for required tools
echo "🔍 Checking build tools..."
if ! python3 -c "import build" 2>/dev/null; then
    echo "📦 Installing build tool..."
    python3 -m pip install --upgrade build
fi

# Build the package
echo "🔨 Building wheel package..."
python3 -m build

# List built packages
echo "✅ Build complete! Generated packages:"
ls -la dist/

# Show package info
echo ""
echo "📋 Package Information:"
if command -v wheel >/dev/null 2>&1; then
    wheel show dist/*.whl
else
    echo "💡 Install 'wheel' package to see detailed package info: pip install wheel"
fi

echo ""
echo "🚀 To install the built package:"
echo "   pip install dist/zero_log_parser-*.whl"
echo ""
echo "📤 To upload to PyPI:"
echo "   python3 -m twine upload dist/*"
echo "   (requires twine: pip install twine)"