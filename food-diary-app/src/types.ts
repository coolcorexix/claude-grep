/**
 * A single food the child has "unlocked" — recorded with a photo on the day
 * they first tried it.
 */
export type FoodEntry = {
  /** Stable unique id, also used as the stored photo's file name. */
  id: string;
  /** Name of the food, e.g. "Avocado". */
  name: string;
  /** Local `file://` URI of the saved photo. */
  photoUri: string;
  /** Optional note — how it went, reaction, rating, etc. */
  note?: string;
  /** ISO timestamp of when the food was unlocked. */
  unlockedAt: string;
};
