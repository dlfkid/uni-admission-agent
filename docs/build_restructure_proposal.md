# Build Pipeline Restructure Proposal

## Current State
- Single artifact per platform containing both backend + extension
- Chrome extension duplicated across Windows, macOS, and Linux releases
- Users must download full package for any updates

## Proposed Structure

### 1. Separate Artifact Types
```
Release Assets:
├── Chrome Extension (cross-platform)
│   └── uni-admission-extension-v1.0.0.zip
└── Backend Executables (platform-specific)
    ├── adm-agent-v1.0.0-windows-x86_64.zip
    ├── adm-agent-v1.0.0-macos-x86_64.tar.gz
    ├── adm-agent-v1.0.0-macos-arm64.tar.gz
    └── adm-agent-v1.0.0-linux-x86_64.tar.gz
```

### 2. Content Separation

#### Chrome Extension Package
- `uni-admission-extension-v{version}.zip`
- Contains: `dist/` folder ready for Chrome "Load unpacked"
- Size: ~50KB (lightweight, UI-only)

#### Backend Packages
Each platform package contains:
- `adm-agent` executable
- `.env.example` 
- `README.txt` with platform-specific instructions
- NO extension files

### 3. Build Script Modifications

```python
# New flags for build_dist.py
--extension-only     # Build only extension artifact
--backend-only       # Build only backend artifact  
--platform=TARGET    # Specific platform (windows, macos, linux)

# Examples:
python scripts/build_dist.py --extension-only
python scripts/build_dist.py --backend-only --platform=macos
```

### 4. Upgrade Implementation Benefits

#### For Backend Upgrade (`./adm-agent upgrade`)
```bash
# Only download platform-specific backend
./adm-agent upgrade
# Downloads: adm-agent-v1.1.0-macos-arm64.tar.gz (5-10MB)
# Instead of: full package with extension (5-10MB + extension)
```

#### For Extension Updates
- Users manually download new extension zip
- Much smaller download (~50KB vs 5-10MB)
- Faster update cycle for UI improvements

### 5. Implementation Plan

#### Phase 1: Modify build_dist.py
1. Add `build_extension_only()` function
2. Add `build_backend_only()` function  
3. Update `package_release()` to handle separation
4. Add command-line flags for selective building

#### Phase 2: Update GitHub Workflow
1. Build extension once (not per platform)
2. Upload extension as separate artifact
3. Keep platform matrix for backends only

#### Phase 3: Implement Upgrade Command
1. `./adm-agent upgrade` downloads only backend
2. `./adm-agent upgrade --check` shows available versions
3. No extension handling in backend upgrade logic

## Benefits

### For Users
- **Faster Updates**: Download only what changed
- **Bandwidth Savings**: Extension is ~50KB, not 5-10MB per platform
- **Selective Updates**: Update backend or frontend independently

### For Development
- **CI Efficiency**: Extension built once, not 3x
- **Release Management**: Clear separation of concerns
- **Testing**: Can test extension and backend changes independently

### For Alpha Distribution
- **Easier Testing**: Send users only what they need
- **Version Control**: Track frontend/backend versions separately
- **Rollback**: Can rollback extension without affecting backend

## Migration Strategy

1. **Backward Compatibility**: Keep current build as default
2. **Gradual Rollout**: New structure available via flags
3. **User Education**: Update README with new download instructions
4. **Version Alignment**: Sync extension and backend version numbers