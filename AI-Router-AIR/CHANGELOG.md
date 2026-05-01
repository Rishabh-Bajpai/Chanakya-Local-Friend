## 2026-03-05

### Added
- Auto-discovery service for local AI providers via TCP and Docker scanning
- Robust proxy engine for forwarding requests to upstream providers
- Admin API endpoints for provider management and discovered provider acceptance
- Comprehensive test coverage for proxy engine, core config, and API endpoints

### Changed
- Enhanced provider configuration management with improved model type inference
- Improved error handling in discovery UI with detailed error messages

### Fixed
- Corrected model extraction from API responses to handle various data formats
- Fixed localhost fallback path to properly infer model capabilities
