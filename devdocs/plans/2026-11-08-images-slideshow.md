# Images Detail slideshow

## Owner scope

Add a small PR for a fullscreen Detail slideshow. A checkable Slideshow button sits immediately after the buffered count; F2 enters/leaves while Images is active. Use 0.5–8 seconds in 0.5-second steps (default 3 seconds), loop through the current filtered gallery, replace the right-hand thumbnail carousel with the info card's prompt/LoRA content in a scrollable rail, and provide translucent controls that become opaque on hover/focus. The wheel changes rate only over controls; Left/Right navigate manually and restart the dwell. No provider/generation behavior, new store or automatic restart.

## Implementation

- `gallery_slideshow.py` is a page-parented QObject with a single-shot timer and canonical `gallery_slideshow.ui` controls. Entry selects Detail and optimized layout, suspends Live/Follow and enters native fullscreen; exit restores previous view/layout/window and Live/Follow mode without jumping to a newer image. F5, navigation away and shutdown stop playback. Escape also leaves after existing popup/inline-feedback precedence.
- Navigation reuses the gallery's bounded step/page query path and retains filters/sort. Both ends loop. No whole-library image enumeration or decoding on Qt. Loading/decoding waits rather than skipping; decoded results restart a full dwell. Read failures retain existing unavailable-image presentation; gallery-query failures stop playback.
- The translucent control group is inset at the image's top right. Forward (default) follows the current filtered sort; Backward reverses it. Changing direction restarts the dwell without moving immediately; manual Left/Right remain previous/next regardless of automatic direction. Direction is session-local, like rate.
- `GalleryHoverCard.set_dock` mounts the same already loaded info card in the canonical right rail, hiding the carousel during playback and restoring its prior visibility on exit. No extra metadata/provider reads. Shared token QSS gives a subdued grey presentation, with scrolling for long information. Controls are 35% opaque at rest and fully opaque during pointer/focus interaction; only their subtree intercepts wheel events.
- Playback/rate are session-local and never restored automatically. Existing convenience preferences are restored on exit; durable saved images/selection authority and cache rules are unchanged.

## Evidence and remaining acceptance

Source/static review only during implementation. No tests, fixtures, renders, screenshots, model downloads, inference or provider calls added/run. CI, owner Windows fullscreen/DPI/focus/opacity, wheel/keyboard handling, long metadata, cross-batch looping, empty/unavailable inputs, navigation/close and exact packaged acceptance remain separate gates. The requested 0.5-second minimum is a configured dwell after decode, not a throughput guarantee.
