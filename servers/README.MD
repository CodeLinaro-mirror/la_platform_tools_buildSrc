Emulator Build Script
=====================

This directory contains the emulator build scripts.
These build scripts are usually invoked from the
android build system when:

- A presubmit run is requested: Triggered when a developer sends
  out a review for any of the registered dependent branches.

- Post submit run is requested: Triggered when any of the branches
  in the manifest has received a new change.

The main script that is invoked is `build_tools.py`

Caching
-------

The emulator build system has support for sccache.

sccache is a [ccache](https://ccache.dev/)-like compiler caching tool.
It is used as a compiler wrapper and avoids compilation when possible,
storing cached results either on [local disk](#local) or in
one of [several cloud storage backends](#storage-options).

The build scripts will configure try to configure sccache as follows:

- When running in GCE:

  The `gs://emu-dev-sccache` bucket will be used to store results.
  This bucket is *only* accessible from within google cloud projects.
  
  The `default` service account associated with the instance
  must be given the following bucket permissions:

  - Storage Object Creator
  - Storage Object Viewer

  Both the windows and linux builders will use this configuration.

- When running in corp network :

  A redis cache instance will be used. A redis service running in
  the emu-dev-cts project will be used to serve cache requests.
  
  The mac build bots will use this configuration.

- When running locally:

  A local cache will be used.

Both the gce and redis backend act as a distributed cache.

At the end of the build you will see status report:

```
01:41:14(15:13.12) scc | /buildbot/src/android/emu-master-dev $> /buildbot/src/android/emu-master-dev/prebuilts/android-emulator-build/common/sccache/linux-x86_64/sccache --stop-server
01:41:14(15:13.67) scc | ============================================================================================================================================
01:41:14(15:13.84) scc | Stopping sccache server...
01:41:14(15:13.80) scc | Compile requests                  10579
01:41:14(15:13.63) scc | Compile requests executed         10553
01:41:14(15:13.17) scc | Cache hits                         8761
01:41:14(15:13.88) scc | Cache hits (C/C++)                 8761
01:41:14(15:13.39) scc | Cache misses                       1792
01:41:14(15:13.3 ) scc | Cache misses (C/C++)               1792
01:41:14(15:13.61) scc | Cache timeouts                        0
01:41:14(15:13.17) scc | Cache read errors                     0
01:41:14(15:13.72) scc | Forced recaches                       0
01:41:14(15:13.27) scc | Cache write errors                    0
01:41:14(15:13.81) scc | Compilation failures                  0
01:41:14(15:13.42) scc | Build completed!
01:41:14(15:13.70) scc | Cache errors                          0
01:41:14(15:13.50) scc | Non-cacheable compilations            0
01:41:14(15:13.15) scc | Non-cacheable calls                  26
01:41:14(15:13.4 ) scc | Non-compilation calls                 0
01:41:14(15:13.10) scc | Unsupported compiler calls            0
01:41:14(15:13.63) scc | Average cache write               0.985 s
01:41:14(15:13.18) scc | Average cache read miss           0.985 s
01:41:14(15:13.67) scc | Average cache read hit            0.000 s
01:41:14(15:13.19) scc | Failed distributed compilations       0
01:41:14(15:13.74) scc | 
01:41:14(15:13.17) scc | Non-cacheable reasons:
01:41:14(15:13.72) scc | unknown source language              26
01:41:14(15:13.24) scc | 
01:41:14(15:13.66) scc | Cache location                  GCS, bucket: Bucket(name=emu-dev-sccache), key_prefix: (none)

```



Here is a sample of the improvements you get from the cache:

| target                    | normal   | sccache  |
|---------------------------|----------|----------|
| emulator-linux_aarch64    | 34:15.57 | 13:19.67 |
| emulator-linux_x64        | 28:15.38 | 13:51.35 |
| sdk_tools_windows         | 22:54.00 |    X     |
| gfxstream_sdk_tools_linux | 15:17.27 | 07:41.28 |


Debugging Cache Performance
---------------------------

You can analyze cache misses by changing the line:
`self.env["SCCACHE_LOG"] = "info"`
to `self.env["SCCACHE_LOG"] = "debug"`

in [server_config.py](server_config.py). 

After a completed build you should see the file sccache.log in the artifacts
that contains detailed information about the cache behavior.

Look for lines containing `CannotCache`, and see if there are
any flags that are not supported by sccache.

Broken Cache
------------

If you suspect that builds are failing due to a broken cache you simply:

- Erase all contents from gs://emu-dev-sccache

```sh
    gsutil -m rm -a gs://emu-dev-sccache/**
```

- Restart the docker container sscache-emu-dev:

```sh
    gcloud compute ssh --zone "us-west1-b" "sccache-emu-dev"  --project "emu-dev-cts"
    docker stop sccache-redis 
    docker rm sccache-redis
    docker run -d -p 443:6379 -v /home/redis:/usr/local/etc/redis --name sccache-redis  redis redis-server /usr/local/etc/redis/redis.conf
```
