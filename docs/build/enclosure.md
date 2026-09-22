---
icon: lucide/box
---

# Enclosure & Case

The IARC v3 case holds the OPS243-A, the sound sensor, the angle radar, the
monitor, and the Raspberry Pi in one printed assembly.

STL, STEP, and 3MF files live in
[`cad/IARC_case/`](https://github.com/jewbetcha/openflight/tree/main/cad/IARC_case)
in the repository.

**Fasteners:** 10 × M5 bolts, 12 × M2.5 bolts.

!!! warning "K-LD7 mounts are for deprecated hardware"

    The KLD7 mounts in this case are for the deprecated K-LD7 angle radars.
    OpenFlight has moved to the TI IWR6843. Skip the KLD7 mount steps for a
    new build.

![The assembled IARC v3 case](images/IARC_case_v3.png)

## Assembly

  
1. Print out `sensor_housing.stl` ![](images/sensor_housing.png)
2. Print out (2x) `OPS_mount.stl` ![](images/ops_mount.png)
3. Secure the OPS sensor to each `OPS_mount.stl` part with screws (I don't know which ones)
4. Secure the `OPS_mount.stl` parts to the `sensor_housing.stl` part with (2x) M5 bolt ![](images/secure_ops_mount.png)
5. Fit the sound sensor into the little slot on the left side of the `sensor_housing.stl` part. ![](images/sound_placement.png)
6. Print out (1x) `KLD7_h_mount_L_v2.stl` and (1x) `KLD7_h_mount_R_v2.stl` ![](images/KLD7_mounts.png)
7. Slot a KLD7 sensor between the mount parts, and secure them to the `sensor_housing.stl` part with (2x) M5 bolt ![](images/bottom_KLD7_mount.png)
8. Print out (1x) `KLD7_v_mount_L_v2.stl` and (1x) `KLD7_v_mount_R_v2.stl` ![](images/KLD7_mounts.png)
6. Slot a KLD7 sensor between the mount parts, and secure them to the `sensor_housing.stl` part with (2x) M5 bolt ![](images/top_KLD7_mount.png)
7. Print out the `monitor_mount.stl` part. ![](images/monitor_mount_print.png)
8. Print out (4x) `monitor_standoffs.stl`'s. ![](images/monitor_standoffs_print.png)
9. Secure each `monitor_standoff.stl` part to the corners of the `monitor_mount.stl` part with (1x) M2.5 bolt. ![](images/monitor_mount_corners.png)
10. Secure the monitor to the `monitor_mount.stl` part. ![](images/monitor_secure.png)
11. Secure the raspberry Pi to the monitor. ![](images/rasp_secure.png)
12. Print out the `monitor_shell.stl` part. If you use the Raspberry Pi Touch Display 2 instead of the 7" HMTECH touchscreen, print `Touch_Display2_shell.stl` and `Touch_Display2_backplate.stl` instead. ![](images/monitor_shell_print.png)
13. Secure the monitor assembly inside the `monitor_shell.stl` part with (4x) M2.5 bolts at the corners. ![](images/monitor_shell_assembly.png)
14. Print out the `monitor_back.stl` part. ![](images/monitor_back.png)
15. Secure the `monitor_back.stl` part to the `monitor_standoffs.stl` parts with (4x) M2.5 bolts. ![](images/monitor_back_secure.png)
16. Slide the whole assembly into the sensor housing assembly, using the dovetails to align everything together. ![](images/whole_assembly.png)
17. Print out the `name_plate.stl` file. ![](images/name_plate_print.png)
18. Secure the `name_plate.stl` file to the front of the whole assembly with glue. ![](images/name_plate_glue.png)
19. Print out the `curvy_backplate.stl` file. ![](images/curvy_backplate_print.png)
20. Secure the `curvy_backplate.stl` file to the whole assembly with (4x) M2.5 bolts. ![](images/curvy_backplate_secure.png)
21. Print out (4x) `case_foot.stl` parts. ![](images/case_foot_print.png)
22. Secure each foot to the bottom of the whole assembly with (4x) M5 Bolts. ![](images/feet_attach.png)

And that’s how you put together the 3d printed parts!
