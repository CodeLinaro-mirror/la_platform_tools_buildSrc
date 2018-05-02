package com.android.tools.internal.testing;

import static org.junit.Assert.assertEquals;

import com.google.common.collect.ImmutableList;
import java.util.concurrent.ForkJoinPool;
import java.util.concurrent.Semaphore;
import org.junit.Test;

public class DevicePoolTest {

    @Test(timeout = 45000)
    public void checkDevicePoolSanity() throws Exception {
        final DevicePool devicePool = new DevicePool();

        checkState(devicePool);
        int count = 20;

        ForkJoinPool requests = new ForkJoinPool(2);
        ForkJoinPool releases = new ForkJoinPool(1);
        try {
            // Test should wait for all count*2 operations to finish, hence -(2 * count)
            // Add one permit so that the test can call acquire() to block until all the devices are
            // released.
            Semaphore completion = new Semaphore(1 - (2 * count));
            for (int i = 0; i < count; i++) {
                final String testName = "test " + i;
                requests.submit(
                        () -> {
                            devicePool.getAllDevices("All devices " + testName);
                            releases.submit(
                                    () -> {
                                        devicePool.returnAllDevices("All devices" + testName);
                                        completion.release();
                                    });
                            return null;
                        });
                requests.submit(
                        () -> {
                            String device =
                                    devicePool.getDevice(
                                            ImmutableList.of("A", "B", "C"),
                                            "Single device " + testName);
                            releases.submit(
                                    () -> {
                                        devicePool.returnDevice(
                                                device, "Single device " + testName);
                                        completion.release();
                                    });
                            return null;
                        });
            }
            completion.acquire();
        } finally {
            requests.shutdownNow();
            releases.shutdownNow();
        }

        checkState(devicePool);
    }

    private static void checkState(DevicePool devicePool) throws InterruptedException {
        assertEquals("A", devicePool.getDevice(ImmutableList.of("A"), "Checkstate A"));
        assertEquals("B", devicePool.getDevice(ImmutableList.of("A", "B"), "Checkstate AB"));
        assertEquals("C", devicePool.getDevice(ImmutableList.of("A", "B", "C"), "Checkstate ABC"));

        devicePool.returnDevice("B", "Checkstate AB");
        devicePool.returnDevice("A", "Checkstate A");
        devicePool.returnDevice("C", "Checkstate ABC");
    }
}
