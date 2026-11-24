// plinius-reboot [TARGET]: syncs and restarts the phone at once, used in the rescue system
// (rescue/rescue-init). With TARGET (e.g. "bootloader") the kernel hands it to the reboot-mode
// driver, which tells the bootloader where to go; busybox reboot cannot pass an argument.
// SPDX-License-Identifier: MIT
#include <linux/reboot.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	sync();
	if (argc > 1)
		syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2, LINUX_REBOOT_CMD_RESTART2, argv[1]);
	else
		syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2, LINUX_REBOOT_CMD_RESTART, NULL);
	// Only reached if the kernel refused (not root)
	perror("plinius-reboot");
	return 1;
}
