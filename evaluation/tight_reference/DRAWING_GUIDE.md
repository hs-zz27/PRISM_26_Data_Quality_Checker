# Drawing guide: tight person outlines

Thank you for drawing these. They become the reference for what a "tight" outline is, so the tool can be set
to the mentor's standard.

## What to do

1. Open this folder in LabelMe (File > Open Dir). Each photo already has COCO's outlines on it.
2. For every person in every photo, fix the outline so it follows the person's visible boundary closely.
3. Save each file (Ctrl+S) under the same name, without changing the photo.
4. Zip the whole folder and send it back.

## How tight

Use the mentor's two good examples as the standard: the bride and the fire performer. In short:

- Follow the body's real outline: the head and hair, ears, arms, fingers, legs and shoes.
- Leave out the background, including gaps between the arms and the body and between the legs.
- Add more points where the outline curves; a few long straight lines are not tight.
- Things the person holds or carries may stay inside (bag, umbrella, racket).
- Small wobbles of a pixel or two are fine.

## Special cases

- **Someone in front hides part of the person:** draw only what you can see. If the visible parts are
  separate, draw each part as its own polygon and give them the same Group ID.
- **People touching:** each outline stops where the other person begins. The two outlines may touch but
  should not cover each other.
- **A person at the edge of the photo:** follow the photo's edge where the person is cut off.
- **A person with no outline:** add one, with the label `person`.
- **Do not** change labels, delete people, or move an outline onto a different person.

## Please do not

- Do not start a new file or rename files: the files are matched by name.
- Do not edit the photos.
- Do not delete the text in the Description field of a shape. It says which COCO person it was.
