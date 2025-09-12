# Image Generation Model Comparison

## Supported Aspect Ratios by Model

### DALL-E 3 ✨
**Native Sizes**: 1024x1024, 1024x1792, 1792x1024  
**Supported Aspect Ratios**:
- `1:1` → 1024x1024 (Square)
- `16:9`, `21:9`, `3:2`, `5:4` → 1792x1024 (Landscape)  
- `9:16`, `9:21`, `2:3`, `4:5` → 1024x1792 (Portrait)

**Note**: DALL-E has limited resolution options, so some aspect ratios are approximated to the closest supported size.

### Stable Diffusion 3 🎨  
**Full Aspect Ratio Support**: All ratios generate at optimal resolutions
- Native support for all aspect ratios: 16:9, 1:1, 21:9, 2:3, 3:2, 4:5, 5:4, 9:16, 9:21

### Replicate (Flux) ⚡
**Full Aspect Ratio Support**: All ratios generate at optimal resolutions  
- Native support for all aspect ratios with high quality output
- Uses black-forest-labs/flux-schnell model

### GPT Image 1 🔄
**Full Aspect Ratio Support + Iteration**: All ratios with edit capabilities
- Native support for all aspect ratios
- **Unique Feature**: Iterate button for editing existing images
- Can refine and modify generated images with text instructions

## Recommendations by Use Case

- **Quick Generation**: DALL-E 3 (fastest, good quality)
- **High Quality**: Stable Diffusion 3 (best balance of quality/features)  
- **Creative Control**: GPT Image 1 (can iterate and refine)
- **Artistic Style**: Replicate (unique artistic outputs)

## Recent Updates ✅

- **DALL-E 3**: Now supports aspect ratio selection (previously only 1:1)
- **Iterate Button**: Fixed timeout issues, now works for up to 10 minutes
- **Error Handling**: Improved feedback for failed generations
- **Resolution Mapping**: Optimized size selection for each model
