import { CameraView, useCameraPermissions } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import React, { useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { addEntry } from '../storage';
import { colors, font, radius, spacing } from '../theme';
import type { FoodEntry } from '../types';

type Props = {
  onCancel: () => void;
  onSaved: (entry: FoodEntry) => void;
};

export function CaptureScreen({ onCancel, onSaved }: Props) {
  const insets = useSafeAreaInsets();
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);

  const [photoUri, setPhotoUri] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  async function handleCapture() {
    if (!cameraRef.current || busy) return;
    try {
      setBusy(true);
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.7 });
      if (photo?.uri) setPhotoUri(photo.uri);
    } catch {
      Alert.alert('Oops', 'Could not take the photo. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  async function handlePickFromLibrary() {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.7,
    });
    if (!result.canceled && result.assets[0]?.uri) {
      setPhotoUri(result.assets[0].uri);
    }
  }

  async function handleSave() {
    if (!photoUri) return;
    if (!name.trim()) {
      Alert.alert('Name the food', 'What did your little one unlock today?');
      return;
    }
    try {
      setBusy(true);
      const entry = await addEntry({
        name,
        tempPhotoUri: photoUri,
        note,
      });
      onSaved(entry);
    } catch {
      Alert.alert('Oops', 'Could not save this food. Please try again.');
      setBusy(false);
    }
  }

  // ---- Permission gate ----
  if (!permission) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={[styles.centered, { paddingTop: insets.top }]}>
        <Text style={styles.permEmoji}>📸</Text>
        <Text style={styles.permTitle}>Camera access needed</Text>
        <Text style={styles.permText}>
          Yum Quest needs the camera to snap a photo of the food being unlocked.
        </Text>
        <Pressable style={styles.primaryBtn} onPress={requestPermission}>
          <Text style={styles.primaryBtnText}>Allow camera</Text>
        </Pressable>
        <Pressable style={styles.linkBtn} onPress={handlePickFromLibrary}>
          <Text style={styles.linkText}>Choose from library instead</Text>
        </Pressable>
        <Pressable style={styles.linkBtn} onPress={onCancel}>
          <Text style={styles.linkText}>Cancel</Text>
        </Pressable>
      </View>
    );
  }

  // ---- Review step: photo captured, fill in details ----
  if (photoUri) {
    return (
      <KeyboardAvoidingView
        style={styles.container}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={[
            styles.reviewContent,
            { paddingTop: insets.top + spacing.lg, paddingBottom: insets.bottom + spacing.xl },
          ]}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={styles.reviewTitle}>New food unlocked! 🎉</Text>
          <Image source={{ uri: photoUri }} style={styles.preview} />

          <Pressable
            style={styles.retakeBtn}
            onPress={() => setPhotoUri(null)}
            disabled={busy}
          >
            <Text style={styles.retakeText}>↺ Retake photo</Text>
          </Pressable>

          <Text style={styles.label}>Food name</Text>
          <TextInput
            style={styles.input}
            placeholder="e.g. Avocado"
            placeholderTextColor={colors.textMuted}
            value={name}
            onChangeText={setName}
            autoCapitalize="words"
            returnKeyType="done"
            maxLength={60}
          />

          <Text style={styles.label}>Note (optional)</Text>
          <TextInput
            style={[styles.input, styles.inputMultiline]}
            placeholder="How did it go? Loved it? Made a face?"
            placeholderTextColor={colors.textMuted}
            value={note}
            onChangeText={setNote}
            multiline
            maxLength={280}
          />

          <Pressable
            style={[styles.primaryBtn, styles.saveBtn, busy && styles.disabled]}
            onPress={handleSave}
            disabled={busy}
          >
            {busy ? (
              <ActivityIndicator color={colors.white} />
            ) : (
              <Text style={styles.primaryBtnText}>Save to diary</Text>
            )}
          </Pressable>
          <Pressable style={styles.linkBtn} onPress={onCancel} disabled={busy}>
            <Text style={styles.linkText}>Cancel</Text>
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    );
  }

  // ---- Camera step ----
  return (
    <View style={styles.cameraContainer}>
      <CameraView ref={cameraRef} style={styles.camera} facing="back" />
      <View style={[styles.topBar, { paddingTop: insets.top + spacing.sm }]}>
        <Pressable style={styles.closeBtn} onPress={onCancel}>
          <Text style={styles.closeText}>✕</Text>
        </Pressable>
        <Text style={styles.cameraHint}>Snap the food 🍴</Text>
        <View style={styles.closeBtn} />
      </View>

      <View style={[styles.controls, { paddingBottom: insets.bottom + spacing.xl }]}>
        <Pressable style={styles.libraryBtn} onPress={handlePickFromLibrary}>
          <Text style={styles.libraryText}>🖼️</Text>
        </Pressable>
        <Pressable
          style={({ pressed }) => [styles.shutter, pressed && styles.shutterPressed]}
          onPress={handleCapture}
          disabled={busy}
        >
          <View style={styles.shutterInner} />
        </Pressable>
        <View style={styles.libraryBtn} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xxl,
    backgroundColor: colors.background,
  },

  // Permission
  permEmoji: { fontSize: 56, marginBottom: spacing.md },
  permTitle: {
    fontSize: font.heading,
    fontWeight: '800',
    color: colors.text,
    marginBottom: spacing.sm,
  },
  permText: {
    fontSize: font.body,
    color: colors.textMuted,
    textAlign: 'center',
    lineHeight: 22,
    marginBottom: spacing.xl,
  },

  // Camera
  cameraContainer: { flex: 1, backgroundColor: '#000' },
  camera: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 },
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.sm,
  },
  closeBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.overlay,
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeText: { color: colors.white, fontSize: 20, fontWeight: '700' },
  cameraHint: {
    color: colors.white,
    fontSize: font.body,
    fontWeight: '700',
    textShadowColor: 'rgba(0,0,0,0.5)',
    textShadowRadius: 4,
  },
  controls: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.xxl,
  },
  shutter: {
    width: 78,
    height: 78,
    borderRadius: 39,
    borderWidth: 5,
    borderColor: colors.white,
    alignItems: 'center',
    justifyContent: 'center',
  },
  shutterPressed: { opacity: 0.7 },
  shutterInner: {
    width: 58,
    height: 58,
    borderRadius: 29,
    backgroundColor: colors.white,
  },
  libraryBtn: {
    width: 52,
    height: 52,
    borderRadius: 16,
    backgroundColor: colors.overlay,
    alignItems: 'center',
    justifyContent: 'center',
  },
  libraryText: { fontSize: 24 },

  // Review
  reviewContent: { paddingHorizontal: spacing.xl },
  reviewTitle: {
    fontSize: font.title,
    fontWeight: '800',
    color: colors.text,
    marginBottom: spacing.lg,
  },
  preview: {
    width: '100%',
    aspectRatio: 1,
    borderRadius: radius.lg,
    backgroundColor: colors.border,
  },
  retakeBtn: { alignSelf: 'center', paddingVertical: spacing.md },
  retakeText: { color: colors.primary, fontWeight: '700', fontSize: font.body },
  label: {
    fontSize: font.small,
    fontWeight: '700',
    color: colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: spacing.sm,
    marginTop: spacing.sm,
  },
  input: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    fontSize: font.body,
    color: colors.text,
    marginBottom: spacing.md,
  },
  inputMultiline: { minHeight: 90, textAlignVertical: 'top' },
  saveBtn: { marginTop: spacing.lg },
  disabled: { opacity: 0.6 },

  // Shared buttons
  primaryBtn: {
    backgroundColor: colors.primary,
    borderRadius: radius.pill,
    paddingVertical: spacing.lg,
    paddingHorizontal: spacing.xxl,
    alignItems: 'center',
    minWidth: 200,
  },
  primaryBtnText: { color: colors.white, fontSize: font.body, fontWeight: '800' },
  linkBtn: { paddingVertical: spacing.md, alignItems: 'center' },
  linkText: { color: colors.textMuted, fontSize: font.body, fontWeight: '600' },
});
