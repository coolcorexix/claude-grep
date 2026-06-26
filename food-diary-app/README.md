# Yum Quest 🍽️

A little mobile diary for a child's eating journey. Each new food your little
one tries gets **"unlocked"** with a photo — building up a playful collection of
everything they've discovered.

Built with **Expo (React Native)**. Everything is stored **on-device** — no
accounts, no servers, no internet required. Photos live in the app's private
storage and entry details are kept in local storage.

## Features

- 📸 **Snap a food** — open the camera and take a photo of the food being unlocked
  (or pick one from your photo library).
- 🏆 **Unlock collection** — a grid of every food discovered, newest first.
- 📝 **Notes** — jot down how it went (loved it, made a face, etc.).
- 🗑️ **Remove** — delete an entry and its photo if you change your mind.

## Running it

You don't need an App Store account to use this yourself — the easiest path is
the **Expo Go** app on your phone.

```bash
cd food-diary-app
npm install
npx expo start
```

Then scan the QR code with **Expo Go** (Android) or the **Camera app** (iOS).

> Note: the live camera requires a real device. The iOS Simulator / Android
> emulator can still run the app, but use **"Choose from library"** there since
> emulators have no camera.

Other launch options:

```bash
npm run android   # build & open on a connected Android device/emulator
npm run ios       # build & open in the iOS Simulator (macOS only)
```

## Project structure

```
food-diary-app/
├── App.tsx                     # root: navigation state + entry list
├── app.json                    # Expo config (name, permissions, plugins)
└── src/
    ├── types.ts                # FoodEntry data model
    ├── theme.ts                # colors / spacing / type scale
    ├── storage.ts              # AsyncStorage + photo persistence (expo-file-system)
    ├── components/
    │   └── FoodCard.tsx        # grid tile
    └── screens/
        ├── CollectionScreen.tsx  # home: the unlock grid + empty state
        ├── CaptureScreen.tsx     # camera → name the food → save
        └── DetailScreen.tsx      # full view of one unlocked food
```

## How data is stored

- **Photos** are copied out of the temporary camera cache into the app's
  permanent document directory (`<documents>/foods/`) via `expo-file-system`.
- **Entries** (name, note, date, photo path) are saved as JSON in
  `@react-native-async-storage/async-storage` under the key `yumquest:entries:v1`.

Because storage is on-device, uninstalling the app removes the diary. Cloud
backup/sync could be added later (e.g. Firebase or Supabase) without changing
the UI layer.

## Ideas for later

- Cloud backup / multi-device sync
- Search & filter (loved / disliked)
- Milestones ("10 foods unlocked!")
- Export the diary as a keepsake PDF
