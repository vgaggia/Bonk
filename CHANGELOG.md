# Changelog

## [2025-05-26] - Major Updates & Bug Fixes

### 🎨 **Image Generation Improvements**
- **DALL-E 3 Aspect Ratios**: Added full aspect ratio support for DALL-E 3
  - Now supports: 1:1, 16:9, 9:16, 4:5, 5:4, 3:2, 2:3, 21:9, 9:21
  - Maps to DALL-E's native sizes: 1024x1024, 1792x1024, 1024x1792
  - All image models now have consistent aspect ratio selection
- **Iterate Button Fix**: Fixed timeout issues with GPT Image 1 iterate functionality
  - Extended timeout from 3 minutes to 10 minutes
  - Better error handling for expired interactions
  - Improved user feedback and retry logic

### 🚀 Added
- **Claude 4 Support**: Updated to latest `claude-sonnet-4-20250514` model
- **Enhanced Documentation**: Comprehensive README with setup instructions
- **Security Template**: `.env.example` file for safe API key management
- **Version Management**: Pinned dependency versions in `requirements.txt`
- **Input Validation**: Better message length and content validation
- **Model Comparison Guide**: Added `MODEL_COMPARISON.md` for image generation models
- **Test Suite**: Added aspect ratio mapping tests

### 🔧 Fixed
- **Security**: Removed unprofessional comments from codebase
- **API Configuration**: Improved error handling for missing API keys
- **Token Limits**: Increased prompt enhancement token limit (100 → 200)
- **Message Validation**: Added length limits and null checks
- **DALL-E Resolution**: Fixed hardcoded 1024x1024 size, now supports all aspect ratios
- **UI Consistency**: All image models now use aspect ratio selection interface

### 🛡️ Security Improvements
- **API Key Protection**: Added validation for missing API keys
- **Input Sanitization**: Enhanced message validation
- **Documentation**: Added security warnings in README

### 📝 Dependencies Updated
- `discord.py>=2.3.0`
- `anthropic>=0.28.0` (for Claude 4 support)
- `openai>=1.30.0`
- `Pillow>=10.0.0` (security updates)
- `requests>=2.31.0` (security updates)
- And more...

### ⚠️ Breaking Changes
- **Claude Model**: Automatically uses Claude 4 instead of Claude 3.5
- **Environment**: Requires new `.env` setup using `.env.example`

### 🎯 Performance
- **Error Handling**: More robust API error recovery
- **Logging**: Improved error tracking and debugging
- **Validation**: Faster input validation checks

---

## Migration Guide

1. **Update Dependencies**:
   ```bash
   pip install -r requirements.txt --upgrade
   ```

2. **Update Environment**:
   ```bash
   cp .env.example .env
   # Then add your actual API keys to .env
   ```

3. **Test Claude 4**:
   - The bot now uses Claude 4 by default
   - Better reasoning and coding capabilities
   - Same API, improved performance

---

*For questions about these changes, check the updated README.md or create an issue.*
