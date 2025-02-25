# Robox-pythonLibs

Robox UF2 source and Python Library.

## Compiling UF2 from source

The `picotool` library is used, which can be found [here](https://github.com/raspberrypi/picotool).

To compile, all source files must be transferred to the Pico's flash memory. This can be done via [Thonny](https://thonny.org).

Once transferred, unplug the Pico and plug it in while holding the BOOTSEL button to boot it in BOOTSEL mode. A removable volume will be mounted to your device, typically under the name `NO-NAME`.

In a command line program, use the following command to extract the UF2 from the Pico:

```bash
picotool save -a <PICO_VOLUME_PATH> <DESTINATION_PATH> -t uf2
```

This will save the *entire* Pico's flash memory onto your device. This UF2 can then be used to flash other Pico devices.