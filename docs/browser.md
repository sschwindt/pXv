# Browser app

Start the app as described in [Installation](installation.md). Video
analysis runs in the browser. The local server reads GeoPackage point layers.

## Import and analyze

1. Select **Import** and choose an MP4, MOV, or M4V containing video your
   browser can decode as H.264 or H.265.
2. Select the video reference. A DJI SRT supplies the camera height above
   takeoff, gimbal attitude, and GPS position. Enter the signed difference
   between takeoff elevation and water elevation in **UAS takeoff above
   water surface (m)**. If you lack an SRT, enter ground control points
   to locate the image on a map, enter a known metric scale for a flat
   image, or work in pixels per second.
3. Optionally add a velocity survey as a GeoPackage point layer, GeoJSON,
   CSV, TSV, or pXv survey JSON. Map the columns for position and either
   speed or two horizontal velocity components. Use metres per second for
   velocity and metres for depth. For a subsurface reading, provide both
   the depth below the surface and the total water depth.
4. Review the coordinate systems and footprint overlap. Enable the proposed
   reprojection to compare different supported coordinate systems. A known
   metric scale alone provides speed units. Map comparison also needs a
   georeferenced video.
5. Set the frame range and the method. Draw water and static bank masks if
   needed, then select **Run analysis**.

## Compare with a survey

Select **Reference only**, **Validation**, or **Calibration and validation**.
The app matches nearby cells to survey positions within the chosen radius.
Calibration needs at least five fit locations and three spatially separate
validation locations per method. The report compares the original and
calibrated RMSE on those same validation locations.

The `speed_error_m_s` column in CSV and XLSX gives the absolute speed
difference at a matched location. It is blank where a reference match is
unavailable. You can change the survey mode and output filters after a run.

## Filter vectors

Use **Maximum speed** to set a physical limit for the site. The Isar
preset starts at 3 m/s. **PIV local median** checks whether each vector
agrees with its neighbours. Start with a threshold of 2 and review
exclusions near bends or shear layers. **PTV tracks per cell** requires
repeat observations from distinct tracers. Raising this setting can reduce
coverage when seeding is sparse.

**Show excluded vectors** marks rejected cells on the map. Use **Help
bubbles** to show or hide guidance for the controls.

## Save settings and export results

Use **Save project** to download a `.pxv` file with frame and engine settings,
masks, scale, the survey, filters, and graph export settings. **Load project**
restores the settings and prompts for the original footage when needed.
Run analysis to regenerate the results.

Select **Export result graph** after a run. Enter the width and height
in pixels and choose WebP or JPEG and the image quality. The export includes
the reference image, velocity vectors, pink survey points, a speed legend,
and a scale bar. Georeferenced exports also show a north arrow.
Use the CSV or XLSX buttons to export the numeric results.
